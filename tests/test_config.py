import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from config import load_settings
from tests.support import clear_settings_env

# The .env format of the old Vertica-only version, as used in production
LEGACY_ENV = r"""
VERTICA_HOST=10.1.2.3
VERTICA_PORT=5433
VERTICA_DB=dwh
VERTICA_USER=andreas
VERTICA_PASSWORD=secret
VERTICA_SCHEMA=sales
VERTICA_CONNECTION_TIMEOUT=30
VERTICA_READ_TIMEOUT=900
VERTICA_SESSION_LABEL=vertica_csv_export
VERTICA_SSL_MODE=disable
VERTICA_SSL_CA=
SQL_FILE=sql/query.sql
OUTPUT_DIR='D:\Reports\Vertica\${yyyy}\${MM}. ${monthName}'
OUTPUT_FILE_PATTERN='${scriptFilename} (${timestamp})'
OUTPUT_TIMESTAMP_PATTERN=dd-MM-yyyy
OUTPUT_FORMATS=csv,xlsx
OUTPUT_ON_CONFLICT=autofix
OUTPUT_ENCODING=utf-8
CSV_DELIMITER=,
FETCH_SIZE=10000
"""

POSTGRES_ENV = """
DB_HOST=localhost
DB_NAME=latihan
DB_USER=postgres
DB_PASSWORD=postgres
DB_SCHEMA=kaggle
"""


class LoadSettingsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = Path(self._tmp.name) / ".env"
        self._environ = mock.patch.dict(os.environ)
        self._environ.start()
        clear_settings_env()

    def tearDown(self):
        self._environ.stop()
        self._tmp.cleanup()

    def load(self, text, encoding="utf-8"):
        self.env.write_text(text, encoding=encoding)
        return load_settings(self.env)

    def test_old_vertica_env_still_works_unchanged(self):
        cfg = self.load(LEGACY_ENV)
        self.assertEqual(cfg.db_type, "vertica")
        self.assertEqual((cfg.host, cfg.port, cfg.database, cfg.user), ("10.1.2.3", 5433, "dwh", "andreas"))
        self.assertEqual((cfg.schema, cfg.connection_timeout, cfg.read_timeout), ("sales", 30, 900))
        self.assertEqual((cfg.session_label, cfg.ssl_mode), ("vertica_csv_export", "disable"))
        self.assertEqual(cfg.output_dir, r"D:\Reports\Vertica\${yyyy}\${MM}. ${monthName}")
        self.assertEqual(cfg.output_file_pattern, "${scriptFilename} (${timestamp})")

    def test_postgres_is_the_default(self):
        cfg = self.load(POSTGRES_ENV)
        self.assertEqual((cfg.db_type, cfg.port, cfg.schema), ("postgres", 5432, "kaggle"))
        self.assertEqual(cfg.session_label, "sql_csv_export")
        self.assertEqual(cfg.output_formats, ("csv", "xlsx"))

    def test_new_names_win_over_old_names(self):
        cfg = self.load(LEGACY_ENV + "DB_TYPE=vertica\nDB_HOST=vertica-new\nDB_READ_TIMEOUT=1800\n")
        self.assertEqual((cfg.host, cfg.read_timeout, cfg.database), ("vertica-new", 1800, "dwh"))

    def test_empty_new_name_falls_back_to_old_name(self):
        cfg = self.load("DB_TYPE=vertica\nDB_HOST=\n" + LEGACY_ENV)
        self.assertEqual(cfg.host, "10.1.2.3")

    def test_db_type_alias_and_invalid_value(self):
        self.assertEqual(self.load(POSTGRES_ENV + "DB_TYPE=PostgreSQL\n").db_type, "postgres")
        with self.assertRaisesRegex(SystemExit, "DB_TYPE must be postgres or vertica"):
            self.load(POSTGRES_ENV + "DB_TYPE=mysql\n")

    def test_ssl_mode_is_checked_per_database(self):
        self.assertEqual(self.load(POSTGRES_ENV + "DB_SSL_MODE=prefer\n").ssl_mode, "prefer")
        with self.assertRaisesRegex(SystemExit, "DB_SSL_MODE"):
            self.load(LEGACY_ENV + "DB_SSL_MODE=prefer\n")

    def test_notepad_bom_is_ignored(self):
        cfg = self.load("DB_TYPE=postgres\n" + POSTGRES_ENV, encoding="utf-8-sig")
        self.assertEqual(cfg.db_type, "postgres")

    def test_placeholder_and_missing_values(self):
        with self.assertRaisesRegex(SystemExit, "DB_HOST is not set"):
            self.load(POSTGRES_ENV.replace("DB_HOST=localhost", "DB_HOST=ISI_HOST"))
        with self.assertRaisesRegex(SystemExit, "DB_PASSWORD is not set"):
            self.load(POSTGRES_ENV.replace("DB_PASSWORD=postgres", "DB_PASSWORD="))

    def test_missing_env_file(self):
        with self.assertRaisesRegex(SystemExit, "No .env file found"):
            load_settings(Path(self._tmp.name) / "missing.env")

    def test_password_is_not_in_repr(self):
        cfg = self.load(POSTGRES_ENV.replace("DB_PASSWORD=postgres", "DB_PASSWORD=Sup3rSecret"))
        self.assertNotIn("Sup3rSecret", repr(cfg))


if __name__ == "__main__":
    unittest.main()
