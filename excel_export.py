"""
Export DataFrames to Excel with formatting:
bold yellow header, Aptos Narrow 11 font, centred cells, and auto-fitted column widths.

Can also be used in other projects:
    from excel_export import export_dataframes_to_excel
    export_dataframes_to_excel("result.xlsx", [(df_stock, "DB STOCK")])
"""
from __future__ import annotations

import datetime as dt
import numbers
import re
from decimal import Decimal
from pathlib import Path

import pandas as pd
import xlsxwriter

EXCEL_MAX_ROWS = 1_048_575          # Excel's row limit minus 1 header row
MAX_COL_WIDTH = 255                 # Excel's maximum column width
DATE_FORMAT = "dd-mm-yyyy"
DATETIME_FORMAT = "dd-mm-yyyy hh:mm:ss"

# HEADER format: Aptos Narrow, 11, bold, yellow
HEADER_STYLE = {
    "bold": True,
    "fg_color": "#FFFF00",
    "border": 0,
    "align": "center",
    "valign": "vcenter",
    "font_name": "Aptos Narrow",
    "font_size": 11,
    "text_wrap": True,
}

# DATA CELL format: Aptos Narrow, 11
BODY_STYLE = {
    "border": 0,
    "align": "center",
    "valign": "vcenter",
    "font_name": "Aptos Narrow",
    "font_size": 11,
}

_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def safe_sheet_name(name: str) -> str:
    """Excel sheet names are at most 31 characters and cannot contain [ ] : * ? / \\"""
    cleaned = _INVALID_SHEET_CHARS.sub("_", str(name)).strip().strip("'")
    return cleaned[:31] or "Sheet1"


def _is_blank(value) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _write_cell(ws, row: int, col: int, value, fmt: dict) -> int:
    """
    Write one cell with an explicit format and return an estimate of its display length.

    Every cell gets its own format, so date columns keep the Aptos Narrow font,
    and text that starts with "=" or looks like a URL is still written as plain text.
    """
    if _is_blank(value):
        ws.write_blank(row, col, None, fmt["body"])
        return 0
    if isinstance(value, bool):
        ws.write_boolean(row, col, value, fmt["body"])
        return 5
    if isinstance(value, dt.datetime):
        ws.write_datetime(row, col, value, fmt["datetime"])
        return len(DATETIME_FORMAT)
    if isinstance(value, dt.date):
        ws.write_datetime(row, col, value, fmt["date"])
        return len(DATE_FORMAT)
    if isinstance(value, (numbers.Integral, numbers.Real, Decimal)):
        number = int(value) if isinstance(value, numbers.Integral) else float(value)
        ws.write_number(row, col, number, fmt["body"])
        return len(f"{number:.11g}")    # approximates Excel's display: 12.0 shows as "12"
    text = str(value)
    ws.write_string(row, col, text, fmt["body"])
    return len(text)


def export_dataframes_to_excel(file_path: str | Path, sheets_data: list) -> Path:
    """
    Export several DataFrames to one Excel file with a custom header and auto-fitted columns.

    Parameters:
    - file_path: output file name or path (e.g. 'output.xlsx')
    - sheets_data: [(df1, 'Sheet1'), (df2, 'Sheet2')]
    """
    file_path = Path(file_path)
    options = {"remove_timezone": True, "nan_inf_to_errors": True}

    with xlsxwriter.Workbook(str(file_path), options) as workbook:
        header_format = workbook.add_format(HEADER_STYLE)
        fmt = {
            "body": workbook.add_format(BODY_STYLE),
            "date": workbook.add_format({**BODY_STYLE, "num_format": DATE_FORMAT}),
            "datetime": workbook.add_format({**BODY_STYLE, "num_format": DATETIME_FORMAT}),
        }

        for df, sheet_name in sheets_data:
            if len(df) > EXCEL_MAX_ROWS:
                raise ValueError(f"{len(df):,} rows exceed the Excel limit ({EXCEL_MAX_ROWS:,})")

            worksheet = workbook.add_worksheet(safe_sheet_name(sheet_name))
            columns = [str(c) for c in df.columns]

            # 1. Header in the top row (0) with the header format
            for i, col in enumerate(columns):
                worksheet.write_string(0, i, col, header_format)

            # 2. Data from row 1 on, tracking the maximum width of each column
            widths = [len(col) for col in columns]
            for r, record in enumerate(df.itertuples(index=False, name=None), start=1):
                for i, value in enumerate(record):
                    length = _write_cell(worksheet, r, i, value, fmt)
                    if length > widths[i]:
                        widths[i] = length

            # 3. Autofit: width = longest content + 2, body format for the whole column
            for i, width in enumerate(widths):
                worksheet.set_column(i, i, min(width + 2, MAX_COL_WIDTH), fmt["body"])

    return file_path


def export_rows_to_excel(file_path: str | Path, header: list[str], rows: list, sheet_name: str) -> Path:
    """Used by main.py: turn a query result (header + rows) into a single Excel sheet."""
    df = pd.DataFrame.from_records(rows, columns=header)
    return export_dataframes_to_excel(file_path, [(df, sheet_name)])
