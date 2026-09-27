"""
Run one or more .sql files against PostgreSQL or Vertica (DB_TYPE in .env)
and save the results as CSV and/or Excel.

Examples:
    python main.py --test                                # check the connection only
    python main.py                                       # use SQL_FILE from .env
    python main.py --sql "sql/Daily Stock.sql"           # one file
    python main.py --sql "D:/Query/A.sql" "D:/Query/B.sql"   # several files
    python main.py --sql "D:/Query/Daily"                # every .sql file in a folder
    python main.py --sql sql/stock.sql --name "Daily Stock"

When a folder is read, files whose names start with "_" are skipped.
A quick way to switch a query off without deleting it: rename it to "_Query Name.sql".

The output format is set with OUTPUT_FORMATS in .env (csv, xlsx, or csv,xlsx).
The Excel sheet name is the .sql file name, unless the SQL file has a comment line:
    -- sheet: DB STOCK
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import db
from config import BASE_DIR, Settings, load_settings, resolve_path
from naming import build_output_path

log = logging.getLogger("sql_export")

SHEET_DIRECTIVE = re.compile(r"^\s*--\s*sheet\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


class ExportError(Exception):
    """Error in one SQL file; the other files are still processed."""


@dataclass
class Result:
    sql_path: Path
    ok: bool
    detail: str


def setup_logging() -> Path:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"export_{datetime.now():%Y%m%d}.log"
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")

    log.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    return log_file


def collect_sql_files(inputs: list[Path]) -> tuple[list[Path], list[Result]]:
    """Expand folders into a list of .sql files. Paths that do not exist are recorded as failures."""
    files: list[Path] = []
    missing: list[Result] = []
    for p in inputs:
        if p.is_dir():
            found = sorted(
                (f for f in p.glob("*.sql") if not f.name.startswith(("_", "~"))),
                key=lambda f: f.name.lower(),
            )
            if not found:
                log.warning("Folder has no active .sql files: %s", p)
            files.extend(found)
        elif p.is_file():
            files.append(p)
        else:
            missing.append(Result(p, False, "File/folder not found"))

    unique: list[Path] = []
    seen: set[Path] = set()
    for f in files:
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique, missing


def read_sql(path: Path) -> str:
    sql = path.read_text(encoding="utf-8-sig").strip().rstrip(";").strip()
    if not sql:
        raise ExportError("SQL file is empty")
    return sql


def connect(cfg: Settings, announce: bool = False):
    """Connection context manager (see db.connection); the connection is always closed after use."""
    if announce:
        log.info("Connecting to %s %s:%s / %s as %s",
                 cfg.db_type, cfg.host, cfg.port, cfg.database, cfg.user)
    return db.connection(cfg)


def test_connection(cfg: Settings) -> None:
    with connect(cfg, announce=True) as conn:
        cur = conn.cursor()
        cur.execute("SELECT version(), current_database(), current_schema(), current_user")
        version, db, schema, user = cur.fetchone()
    log.info("Connection successful")
    log.info("  Version : %s", version)
    log.info("  Database: %s | Active schema: %s | User: %s", db, schema, user)


def check_connection(cfg: Settings) -> None:
    """Quick check before starting, so a VPN that is off does not cause a timeout on every file."""
    with connect(cfg, announce=True) as conn:
        conn.cursor().execute("SELECT 1")
    if cfg.schema:
        log.info("Connection OK, schema: %s", cfg.schema)
    else:
        log.info("Connection OK")


def reserve_output_base(cfg: Settings, script_name: str) -> Path:
    """
    Decide the file name without extension. With autofix, the _1, _2, ... suffix is chosen
    so that the CSV and Excel files of one query always share the same name.
    """
    csv_path = build_output_path(
        cfg.output_dir, cfg.output_file_pattern, cfg.output_timestamp_pattern,
        script_name=script_name, base_dir=BASE_DIR,
    )
    base = csv_path.with_name(csv_path.name[: -len(".csv")])
    base.parent.mkdir(parents=True, exist_ok=True)
    if cfg.on_conflict == "overwrite":
        return base

    candidate, i = base, 0
    while any(candidate.with_name(f"{candidate.name}.{ext}").exists() for ext in cfg.output_formats):
        i += 1
        candidate = base.with_name(f"{base.name}_{i}")
    return candidate


def finalize(tmp: Path, final: Path) -> None:
    try:
        tmp.replace(final)
    except PermissionError:
        raise ExportError(
            f"Cannot save {final.name}: the file is open in another program "
            "(e.g. Excel). Close it and run again."
        ) from None


def export_query(cfg: Settings, sql_path: Path, name: str | None, excel) -> tuple[list[Path], int, str]:
    sql = read_sql(sql_path)
    script_name = name or sql_path.stem
    match = SHEET_DIRECTIVE.search(sql)
    sheet_name = match.group(1) if match else script_name

    base = reserve_output_base(cfg, script_name)
    targets = {ext: base.with_name(f"{base.name}.{ext}") for ext in cfg.output_formats}
    temps = {ext: base.with_name(f"~{base.name}.part.{ext}") for ext in targets}

    start = time.perf_counter()
    total = 0
    excel_rows: list | None = [] if "xlsx" in targets else None
    note = ""
    written: list[Path] = []

    try:
        with connect(cfg) as conn:
            cur = db.execute(conn, cfg, sql)
            if cur.description is None:
                raise ExportError("The query returned no result set (is it a SELECT?)")
            header = [col[0] for col in cur.description]

            # Write to temporary files first, so that a failure midway
            # never leaves a half-written file under the final name.
            with contextlib.ExitStack() as stack:
                writer = None
                if "csv" in targets:
                    f = stack.enter_context(temps["csv"].open("w", newline="", encoding=cfg.output_encoding))
                    writer = csv.writer(f, delimiter=cfg.csv_delimiter, quoting=csv.QUOTE_MINIMAL)
                    writer.writerow(header)

                while True:
                    rows = cur.fetchmany(cfg.fetch_size)
                    if not rows:
                        break
                    if writer:
                        writer.writerows(rows)
                    if excel_rows is not None:
                        if total + len(rows) > excel.EXCEL_MAX_ROWS:
                            excel_rows = None
                            note = "Excel skipped: the result exceeds Excel's row limit"
                            log.warning("    %s", note)
                            if writer is None:
                                raise ExportError(note + " and CSV output is disabled")
                        else:
                            excel_rows.extend(rows)
                    total += len(rows)
                    log.info("    ... %s rows", f"{total:,}")

        if "csv" in targets:
            finalize(temps["csv"], targets["csv"])
            written.append(targets["csv"])
            log.info("    CSV    : %s", targets["csv"])

        if excel_rows is not None:
            excel.export_rows_to_excel(temps["xlsx"], header, excel_rows, sheet_name)
            finalize(temps["xlsx"], targets["xlsx"])
            written.append(targets["xlsx"])
            log.info("    Excel  : %s (sheet: %s)", targets["xlsx"], excel.safe_sheet_name(sheet_name))
    finally:
        for tmp in temps.values():
            tmp.unlink(missing_ok=True)

    log.info("    Done: %s rows, %s columns, %.1f s", f"{total:,}", len(header), time.perf_counter() - start)
    return written, total, note


def short(msg: object, limit: int = 160) -> str:
    text = " ".join(str(msg).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def run_exports(cfg: Settings, inputs: list[Path], name: str | None) -> int:
    files, results = collect_sql_files(inputs)
    for r in results:
        log.error("Not found: %s", r.sql_path)

    if not files:
        log.error("No .sql files to run.")
        return 1
    if name and len(files) > 1:
        log.error("--name can only be used with a single SQL file (got %d files).", len(files))
        return 1

    excel = None
    if "xlsx" in cfg.output_formats:
        try:
            import excel_export as excel
        except ImportError as e:
            log.error("The library for Excel output is not installed: %s", e.name)
            log.error("Run setup.bat (or ./setup.sh) again to install the requirements.")
            return 1

    check_connection(cfg)
    log.info("Running %d SQL file(s) (output: %s)", len(files), ", ".join(cfg.output_formats))

    for i, sql_path in enumerate(files, 1):
        log.info("[%d/%d] %s", i, len(files), sql_path.name)
        try:
            written, rows, note = export_query(cfg, sql_path, name, excel)
            exts = ", ".join(p.suffix.lstrip(".") for p in written)
            detail = f"{written[0].stem} [{exts}] ({rows:,} rows)"
            results.append(Result(sql_path, True, f"{detail} - {note}" if note else detail))
        except db.QueryFailed as e:
            log.error("    Query error: %s", e)
            results.append(Result(sql_path, False, f"Query error: {short(e)}"))
        except db.ConnectionFailed as e:
            log.error("    Connection lost: %s", e)
            results.append(Result(sql_path, False, f"Connection lost: {short(e)}"))
        except ExportError as e:
            log.error("    %s", e)
            results.append(Result(sql_path, False, str(e)))
        except Exception as e:  # noqa: BLE001
            log.exception("    Unexpected error: %s", e)
            results.append(Result(sql_path, False, f"Error: {short(e)}"))

    ok = sum(r.ok for r in results)
    failed = len(results) - ok
    log.info("===== Summary: %d succeeded, %d failed =====", ok, failed)
    width = max(len(r.sql_path.name) for r in results)
    for r in results:
        log.info("  %-4s %-*s  %s", "OK" if r.ok else "FAIL", width, r.sql_path.name, r.detail)
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Export PostgreSQL/Vertica query results to CSV and Excel")
    parser.add_argument("--test", action="store_true", help="Only test the connection")
    parser.add_argument("--sql", nargs="+", metavar="PATH",
                        help="One or more .sql files and/or folders (default: SQL_FILE in .env)")
    parser.add_argument("--name", help="Replaces ${scriptFilename} in the file name (single file only)")
    args = parser.parse_args()

    log_file = setup_logging()

    try:
        cfg = load_settings()
        if args.test:
            test_connection(cfg)
            return 0
        inputs = [resolve_path(p) for p in args.sql] if args.sql else [cfg.sql_file]
        code = run_exports(cfg, inputs, args.name)
    except db.ConnectionFailed as e:
        log.error("Could not connect to the server: %s", e)
        log.error(db.connection_hint(str(e)))
        code = 1
    except SystemExit as e:
        # Configuration errors ("[config] ...", "[naming] ...") go to the log file too,
        # which matters when Task Scheduler runs the tool and nobody watches the screen.
        if not isinstance(e.code, str):
            raise
        log.error("%s", e.code)
        code = 1
    except Exception as e:  # noqa: BLE001
        log.exception("Unexpected error: %s", e)
        code = 1

    if code != 0:
        log.error("Details saved in %s", log_file)
    return code


if __name__ == "__main__":
    sys.exit(main())
