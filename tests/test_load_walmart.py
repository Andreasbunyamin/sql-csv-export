"""demo/load_walmart.py: loading the Kaggle Walmart dataset, with a stub psycopg2."""
import contextlib
import datetime as dt
import importlib.util
import io
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import db
from tests.support import make_settings

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("load_walmart", ROOT / "demo" / "load_walmart.py")
loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loader)

SAMPLE = (b"Store,Date,Weekly_Sales,Holiday_Flag,Temperature,Fuel_Price,CPI,Unemployment\r\n"
          b"1,05-02-2010,1643690.9,0,42.31,2.572,211.0963582,8.106\r\n"
          b"1,12-02-2010,1641957.44,1,38.51,2.548,211.2421698,8.106\r\n")


def fake_psycopg2(result=(dt.date(2010, 2, 5), dt.date(2012, 10, 26), 6435)):
    module = types.ModuleType("psycopg2")
    module.Error = type("Error", (Exception,), {})
    cursor = mock.MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = result
    cursor.copy_expert.side_effect = lambda sql, stream: setattr(cursor, "copied", stream.read())
    conn = mock.MagicMock()
    conn.cursor.return_value = cursor
    module.connect = mock.MagicMock(return_value=conn)
    return module, conn, cursor


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._data_dir = mock.patch.object(loader, "DATA_DIR", self.dir)
        self._data_dir.start()
        self.cfg = make_settings(self.dir, db_type="postgres", schema="kaggle")

    def tearDown(self):
        self._data_dir.stop()
        self._tmp.cleanup()

    def test_finds_the_file_or_explains_where_to_get_it(self):
        with self.assertRaisesRegex(SystemExit, "kaggle.com/datasets/yasserh/walmart-dataset"):
            loader.find_source(None)
        with zipfile.ZipFile(self.dir / "archive.zip", "w") as z:
            z.writestr("Walmart.csv", SAMPLE)
        self.assertEqual(loader.find_source(None).name, "archive.zip")
        (self.dir / "Walmart.csv").write_bytes(SAMPLE)
        self.assertEqual(loader.find_source(None).name, "Walmart.csv")  # the CSV wins over a zip
        with self.assertRaisesRegex(SystemExit, "File not found"):
            loader.find_source(str(self.dir / "missing.csv"))

    def test_reads_the_kaggle_zip_directly(self):
        with zipfile.ZipFile(self.dir / "archive.zip", "w") as z:
            z.writestr("Walmart.csv", SAMPLE)
        self.assertEqual(loader.read_csv_bytes(self.dir / "archive.zip"), SAMPLE)
        with zipfile.ZipFile(self.dir / "other.zip", "w") as z:
            z.writestr("notes.txt", b"x")
        with self.assertRaisesRegex(SystemExit, "No Walmart.csv"):
            loader.read_csv_bytes(self.dir / "other.zip")

    def test_header_check(self):
        loader.check_header(b"\xef\xbb\xbf" + SAMPLE)  # a BOM from Excel/Notepad is fine
        with self.assertRaisesRegex(SystemExit, "Unexpected columns"):
            loader.check_header(b"order_id,amount\n1,2\n")

    def test_load_creates_the_table_reads_day_first_dates_and_copies_the_file(self):
        psycopg2, conn, cursor = fake_psycopg2()
        with mock.patch.dict(sys.modules, {"psycopg2": psycopg2}):
            result = loader.load(self.cfg, SAMPLE)
        self.assertEqual(result, loader.EXPECTED)
        psycopg2.connect.assert_called_once_with(**db.postgres_params(self.cfg))
        statements = [c.args[0] for c in cursor.execute.call_args_list]
        # statements that really run, not text inside SQL comments
        self.assertRegex(statements[0], r"(?m)^CREATE TABLE IF NOT EXISTS kaggle\.walmart_sales \(")
        self.assertRegex(statements[0], r"(?m)^COMMENT ON TABLE kaggle\.walmart_sales IS")  # credit kept in the database
        self.assertEqual(statements[1:3], ["TRUNCATE kaggle.walmart_sales", "SET LOCAL datestyle TO 'ISO, DMY'"])
        copy_sql = cursor.copy_expert.call_args.args[0]
        self.assertIn("COPY kaggle.walmart_sales (store, date, weekly_sales, holiday_flag", copy_sql)
        self.assertIn("FORMAT csv, HEADER true", copy_sql)
        self.assertEqual(cursor.copied, SAMPLE)
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    def test_connection_failure_is_explained(self):
        psycopg2, _, _ = fake_psycopg2()
        psycopg2.connect.side_effect = psycopg2.Error('password authentication failed for user "postgres"')
        with mock.patch.dict(sys.modules, {"psycopg2": psycopg2}):
            with self.assertRaisesRegex(SystemExit, "Could not connect(.|\n)*DB_PASSWORD"):
                loader.load(self.cfg, SAMPLE)

    def test_main_checks_database_type_and_row_count(self):
        (self.dir / "Walmart.csv").write_bytes(SAMPLE)
        out = io.StringIO()
        with mock.patch.object(loader, "load_settings", return_value=self.cfg), contextlib.redirect_stdout(out):
            with mock.patch.object(loader, "load", return_value=loader.EXPECTED):
                self.assertEqual(loader.main([]), 0)
            with mock.patch.object(loader, "load", return_value=("2010-05-02", "2012-10-26", 6435)):
                self.assertEqual(loader.main([]), 1)  # e.g. dates read month-first
        self.assertIn("Walmart Dataset by M Yasser H (Kaggle)", out.getvalue())
        self.assertIn("Warning", out.getvalue())
        vertica = make_settings(self.dir, db_type="vertica")
        with mock.patch.object(loader, "load_settings", return_value=vertica):
            with self.assertRaisesRegex(SystemExit, "only supports PostgreSQL"):
                loader.main([])


if __name__ == "__main__":
    unittest.main()
