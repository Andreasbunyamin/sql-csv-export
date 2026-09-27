import socket
import ssl
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import db
from tests.support import make_settings


def fake_psycopg2():
    module = types.ModuleType("psycopg2")
    module.Error = type("Error", (Exception,), {})
    module.connect = mock.MagicMock()
    return module


def fake_vertica_python():
    module = types.ModuleType("vertica_python")
    errors = types.ModuleType("vertica_python.errors")
    errors.Error = type("Error", (Exception,), {})
    errors.DatabaseError = type("DatabaseError", (errors.Error,), {})
    errors.ConnectionError = type("ConnectionError", (errors.DatabaseError,), {})
    errors.QueryError = type("QueryError", (errors.DatabaseError,), {})
    module.errors = errors
    module.connect = mock.MagicMock()
    return module


class TmpTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class ConnectionParamsTests(TmpTestCase):
    def test_postgres_params(self):
        ca = self.dir / "ca.pem"
        cfg = make_settings(self.dir, db_type="postgres", host="db.local", port=5432, database="latihan",
                            user="postgres", password="pw", schema="kaggle", connection_timeout=15,
                            read_timeout=900, session_label="sql_csv_export", ssl_mode="verify-full",
                            ssl_ca=str(ca))
        self.assertEqual(db.postgres_params(cfg), {
            "host": "db.local", "port": 5432, "dbname": "latihan", "user": "postgres", "password": "pw",
            "connect_timeout": 15, "application_name": "sql_csv_export", "sslmode": "verify-full",
            "options": "-c statement_timeout=900000 -c search_path=kaggle,public",
            "sslrootcert": str(ca),
        })

    def test_postgres_params_without_timeout_and_schema(self):
        cfg = make_settings(self.dir, db_type="postgres", read_timeout=0, schema="")
        self.assertNotIn("options", db.postgres_params(cfg))

    def test_vertica_params_same_as_old_version_except_timeout(self):
        cfg = make_settings(self.dir, db_type="vertica", host="10.1.2.3", port=5433, database="dwh",
                            user="andreas", password="pw", connection_timeout=30, read_timeout=900,
                            session_label="vertica_csv_export", ssl_mode="disable")
        self.assertEqual(db.vertica_params(cfg), {
            "host": "10.1.2.3", "port": 5433, "database": "dwh", "user": "andreas", "password": "pw",
            "connection_timeout": 900,  # was 30: vertica-python ignores read_timeout
            "session_label": "vertica_csv_export", "unicode_error": "strict", "autocommit": True,
            "tlsmode": "disable",
        })
        self.assertIsNone(db.vertica_params(make_settings(self.dir, db_type="vertica", read_timeout=0))
                          ["connection_timeout"])

    def test_vertica_ssl_modes(self):
        expected = {  # mode: (check_hostname, verify_mode)
            "require": (False, ssl.CERT_NONE),
            "verify-ca": (False, ssl.CERT_REQUIRED),
            "verify-full": (True, ssl.CERT_REQUIRED),
        }
        for mode, (check_hostname, verify_mode) in expected.items():
            with self.subTest(mode=mode):
                params = db.vertica_params(make_settings(self.dir, db_type="vertica", ssl_mode=mode))
                self.assertNotIn("tlsmode", params)
                self.assertEqual((params["ssl"].check_hostname, params["ssl"].verify_mode),
                                 (check_hostname, verify_mode))


class ExecuteTests(TmpTestCase):
    def test_postgres_select_uses_server_side_cursor(self):
        server_cursor = mock.MagicMock(description=None)
        batches = [[(1,), (2,)], [(3,)], []]

        def fetchmany(size):
            server_cursor.description = (("store",),)  # psycopg2: only set after the first fetch
            return batches.pop(0)

        server_cursor.fetchmany.side_effect = fetchmany
        conn = mock.MagicMock()
        conn.cursor.return_value = server_cursor
        cfg = make_settings(self.dir, db_type="postgres", fetch_size=2)

        cur = db.execute(conn, cfg, "-- sheet: DB STOCK\nSELECT store FROM kaggle.walmart_sales")
        self.assertTrue(conn.cursor.call_args.kwargs["name"].startswith("sql_export_"))
        self.assertEqual(server_cursor.itersize, 2)
        self.assertEqual(cur.description, (("store",),))  # available right away, like a regular cursor
        self.assertEqual([cur.fetchmany(2), cur.fetchmany(2), cur.fetchmany(2)], [[(1,), (2,)], [(3,)], []])

    def test_postgres_multi_statement_uses_plain_cursor(self):
        conn = mock.MagicMock()
        db.execute(conn, make_settings(self.dir, db_type="postgres"), "SET work_mem = '64MB'; SELECT 1")
        conn.cursor.assert_called_once_with()

    def test_vertica_uses_plain_cursor(self):
        conn = mock.MagicMock()
        cur = db.execute(conn, make_settings(self.dir, db_type="vertica"), "SELECT 1")
        conn.cursor.assert_called_once_with()
        cur.execute.assert_called_once_with("SELECT 1")

    def test_single_select_detection(self):
        for sql in ("SELECT 1", "with x as (select 1) select * from x;", "-- a\n/* b; */ SELECT 1"):
            with self.subTest(sql=sql):
                self.assertTrue(db.is_single_select(sql))
        for sql in ("SET x = 1; SELECT 1", "SELECT 'a;b'", "UPDATE t SET a = 1", ""):
            with self.subTest(sql=sql):
                self.assertFalse(db.is_single_select(sql))


class ConnectionTests(TmpTestCase):
    def test_postgres_read_only_session_closed_and_errors_translated(self):
        psycopg2 = fake_psycopg2()
        conn = mock.MagicMock(closed=0)
        psycopg2.connect.return_value = conn
        cfg = make_settings(self.dir, db_type="postgres")
        with mock.patch.dict(sys.modules, {"psycopg2": psycopg2}):
            with self.assertRaisesRegex(db.QueryFailed, "does not exist"):
                with db.connection(cfg):
                    raise psycopg2.Error('relation "kaggle.missing_table" does not exist')
            conn.set_session.assert_called_once_with(readonly=True)
            conn.close.assert_called_once()

            conn.closed = 2  # psycopg2 marks a dropped connection with closed != 0
            with self.assertRaises(db.ConnectionFailed):
                with db.connection(cfg):
                    raise psycopg2.Error("server closed the connection unexpectedly")

            psycopg2.connect.side_effect = psycopg2.Error("Connection refused")
            with self.assertRaisesRegex(db.ConnectionFailed, "refused"):
                with db.connection(cfg):
                    pass

    def test_other_errors_pass_through_and_connection_still_closes(self):
        psycopg2 = fake_psycopg2()
        conn = mock.MagicMock(closed=0)
        psycopg2.connect.return_value = conn
        with mock.patch.dict(sys.modules, {"psycopg2": psycopg2}):
            with self.assertRaises(PermissionError):
                with db.connection(make_settings(self.dir, db_type="postgres")):
                    raise PermissionError("file is open in Excel")
        conn.close.assert_called_once()

    def test_vertica_checks_server_first_then_sets_search_path(self):
        vertica_python = fake_vertica_python()
        conn = mock.MagicMock()
        vertica_python.connect.return_value = conn
        cfg = make_settings(self.dir, db_type="vertica", host="10.1.2.3", port=5433, schema="sales",
                            connection_timeout=30, read_timeout=900)
        with mock.patch.dict(sys.modules, {"vertica_python": vertica_python}), \
                mock.patch("db.probe_server") as probe:
            with db.connection(cfg):
                pass
        probe.assert_called_once_with("10.1.2.3", 5433, 30)
        self.assertEqual(vertica_python.connect.call_args.kwargs["connection_timeout"], 900)
        conn.cursor.return_value.execute.assert_called_once_with("SET SEARCH_PATH TO sales, public")
        conn.close.assert_called_once()

    def test_vertica_errors_are_translated(self):
        vertica_python = fake_vertica_python()
        vertica_python.connect.return_value = mock.MagicMock()
        cfg = make_settings(self.dir, db_type="vertica")
        with mock.patch.dict(sys.modules, {"vertica_python": vertica_python}), mock.patch("db.probe_server"):
            with self.assertRaises(db.QueryFailed):
                with db.connection(cfg):
                    raise vertica_python.errors.QueryError("Syntax error at or near")
            with self.assertRaises(db.ConnectionFailed):
                with db.connection(cfg):
                    raise vertica_python.errors.ConnectionError("Connection closed by Vertica")

    def test_missing_driver_says_what_to_install(self):
        with mock.patch.dict(sys.modules, {"vertica_python": None}):
            with self.assertRaisesRegex(SystemExit, "setup.bat vertica"):
                with db.connection(make_settings(self.dir, db_type="vertica")):
                    pass

    def test_unreachable_server_fails_fast(self):
        with socket.socket() as s:  # a port that is surely unused
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with self.assertRaisesRegex(db.ConnectionFailed, "Cannot reach server"):
            db.probe_server("127.0.0.1", port, 2)

    def test_connection_hints(self):
        self.assertIn("DB_PASSWORD", db.connection_hint('password authentication failed for user "x"'))
        self.assertIn("DB_PASSWORD", db.connection_hint("Invalid username or password"))
        self.assertIn("DB_NAME", db.connection_hint('database "no_such_db" does not exist'))
        self.assertIn("VPN", db.connection_hint("timed out"))


if __name__ == "__main__":
    unittest.main()
