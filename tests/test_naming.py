"""naming.py keeps the behaviour of the Vertica-only version; these tests lock it in."""
import unittest
from datetime import datetime
from pathlib import Path

from naming import build_output_path, format_java_date

WHEN = datetime(2026, 9, 26, 7, 5, 9)


class NamingTests(unittest.TestCase):
    def test_java_date_patterns(self):
        self.assertEqual(format_java_date("dd-MM-yyyy", WHEN), "26-09-2026")
        self.assertEqual(format_java_date("yyyyMMddHHmm", WHEN), "202609260705")
        self.assertEqual(format_java_date("dd MMMM yyyy", WHEN), "26 September 2026")

    def test_folder_variables_and_dbeaver_file_name(self):
        path = build_output_path("output/${yyyy}/${MM}. ${monthName}", "${scriptFilename} (${timestamp})",
                                 "dd-MM-yyyy", script_name="walmart_monthly_sales",
                                 base_dir=Path("/proj"), when=WHEN)
        self.assertEqual(path, Path("/proj/output/2026/09. September/walmart_monthly_sales (26-09-2026).csv"))

    def test_unknown_variable_is_reported(self):
        with self.assertRaisesRegex(SystemExit, r"\$\{table\}"):
            build_output_path("output", "${table}", "dd-MM-yyyy", script_name="x", base_dir=Path("/proj"))


if __name__ == "__main__":
    unittest.main()
