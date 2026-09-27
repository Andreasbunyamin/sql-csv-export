"""End-to-end: the main.py export flow, with SQLite standing in for the database."""
import contextlib
import csv
import io
import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import db
import main
from naming import MONTHS
from tests.support import (clear_settings_env, create_demo_db, drop_sqlite, make_settings,
                           reset_logging, use_sqlite)


class RunExportsTests(unittest.TestCase):
    def setUp(self):
        use_sqlite()
        logging.getLogger("sql_export").addHandler(logging.NullHandler())
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        create_demo_db(self.dir / "demo.db")
        self.sql = self.dir / "sql"
        self.sql.mkdir()
        (self.sql / "01_sales.sql").write_text("-- sheet: DB SALES\nSELECT * FROM sales ORDER BY store;\n")
        (self.sql / "02_top.sql").write_text("SELECT store, amount FROM sales ORDER BY amount DESC LIMIT 1")
        (self.sql / "_disabled.sql").write_text("SELECT 1")  # starts with _ = skipped
        self.cfg = make_settings(self.dir)
        now = datetime.now()
        self.folder = self.dir / "output" / f"{now:%Y}" / f"{now:%m}. {MONTHS[now.month - 1]}"
        self.stamp = f"{now:%d-%m-%Y}"

    def tearDown(self):
        reset_logging()
        drop_sqlite()
        self._tmp.cleanup()

    def names(self):
        return sorted(p.name for p in self.folder.iterdir())

    def test_csv_and_excel_like_the_office_version(self):
        import openpyxl

        self.assertEqual(main.run_exports(self.cfg, [self.sql], None), 0)
        self.assertEqual(self.names(), [f"01_sales ({self.stamp}).csv", f"01_sales ({self.stamp}).xlsx",
                                        f"02_top ({self.stamp}).csv", f"02_top ({self.stamp}).xlsx"])
        with (self.folder / f"02_top ({self.stamp}).csv").open(newline="", encoding="utf-8") as fh:
            self.assertEqual(list(csv.reader(fh)), [["store", "amount"], ["5", "5002.5"]])

        book = openpyxl.load_workbook(self.folder / f"01_sales ({self.stamp}).xlsx")
        self.assertEqual(book.sheetnames, ["DB SALES"])  # from the -- sheet: comment
        header = book["DB SALES"]["A1"]
        self.assertEqual(header.value, "store")
        self.assertTrue(header.font.bold)
        self.assertEqual(header.font.name, "Aptos Narrow")
        self.assertEqual(header.fill.fgColor.rgb, "FFFFFF00")  # yellow header
        self.assertEqual(book["DB SALES"]["C2"].value, 1000.5)  # numbers stay numbers
        self.assertEqual(openpyxl.load_workbook(self.folder / f"02_top ({self.stamp}).xlsx").sheetnames,
                         ["02_top"])

    def test_second_run_autofixes_csv_and_excel_together(self):
        main.run_exports(self.cfg, [self.sql / "02_top.sql"], None)
        main.run_exports(self.cfg, [self.sql / "02_top.sql"], None)
        self.assertIn(f"02_top ({self.stamp})_1.csv", self.names())
        self.assertIn(f"02_top ({self.stamp})_1.xlsx", self.names())

    def test_name_option(self):
        main.run_exports(self.cfg, [self.sql / "02_top.sql"], "Daily Stock")
        self.assertIn(f"Daily Stock ({self.stamp}).csv", self.names())

    def test_failures_do_not_stop_the_rest(self):
        (self.sql / "00_broken.sql").write_text("SELECT * FROM missing_table")
        code = main.run_exports(self.cfg, [self.sql, self.dir / "missing.sql"], None)
        self.assertEqual(code, 1)
        self.assertEqual(len(self.names()), 4)  # the other two queries are still exported

    def test_excel_skipped_above_row_limit_but_csv_complete(self):
        import excel_export
        with mock.patch.object(excel_export, "EXCEL_MAX_ROWS", 3):
            code = main.run_exports(self.cfg, [self.sql / "01_sales.sql"], None)
        self.assertEqual(code, 0)
        self.assertEqual(self.names(), [f"01_sales ({self.stamp}).csv"])
        lines = (self.folder / f"01_sales ({self.stamp}).csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 6)  # header + 5 rows

    def test_no_half_written_file_when_query_fails_midway(self):
        def connect_with_failing_function(cfg):
            conn = sqlite3.connect(cfg.database)

            def boom(store):
                if store == 4:
                    raise RuntimeError("connection lost")
                return store
            conn.create_function("boom", 1, boom)
            return conn

        db._CONNECTORS["sqlite"] = connect_with_failing_function
        (self.sql / "03_fails_midway.sql").write_text("SELECT boom(store) AS store FROM sales ORDER BY store")
        code = main.run_exports(self.cfg, [self.sql / "03_fails_midway.sql"], None)
        self.assertEqual(code, 1)
        self.assertEqual(self.names(), [])  # no .csv, .xlsx or ~.part file left behind


class MainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.env = self.dir / ".env"
        self._environ = mock.patch.dict(os.environ)
        self._environ.start()
        clear_settings_env()

    def tearDown(self):
        reset_logging()
        self._environ.stop()
        self._tmp.cleanup()

    def run_main(self, *args):
        out = io.StringIO()
        with mock.patch("config.ENV_FILE", self.env), mock.patch.object(main, "BASE_DIR", self.dir), \
                mock.patch.object(sys, "argv", ["main.py", *args]), contextlib.redirect_stdout(out):
            code = main.main()
        log_text = "".join(p.read_text(encoding="utf-8") for p in (self.dir / "logs").glob("*.log"))
        return code, log_text

    def test_config_error_is_written_to_the_log_file(self):
        self.env.write_text("DB_TYPE=mysql\n", encoding="utf-8")
        code, log_text = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("[config] DB_TYPE must be postgres or vertica", log_text)

    def test_connection_failure_gets_a_specific_hint(self):
        self.env.write_text("DB_HOST=localhost\nDB_NAME=latihan\nDB_USER=postgres\nDB_PASSWORD=wrong\n",
                            encoding="utf-8")
        refuse = mock.Mock(side_effect=db.ConnectionFailed('password authentication failed for user "postgres"'))
        with mock.patch.dict(db._CONNECTORS, {"postgres": refuse}):
            code, log_text = self.run_main("--test")
        self.assertEqual(code, 1)
        self.assertIn("Could not connect to the server", log_text)
        self.assertIn("Check DB_USER and DB_PASSWORD", log_text)

    def test_missing_driver_is_explained(self):
        self.env.write_text("DB_HOST=localhost\nDB_NAME=latihan\nDB_USER=postgres\nDB_PASSWORD=x\n",
                            encoding="utf-8")
        with mock.patch.dict(sys.modules, {"psycopg2": None}):
            code, log_text = self.run_main("--test")
        self.assertEqual(code, 1)
        self.assertIn("The psycopg2 library is not installed", log_text)


if __name__ == "__main__":
    unittest.main()
