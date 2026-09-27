"""
Load the Kaggle "Walmart Dataset" into PostgreSQL as kaggle.walmart_sales.

Dataset: Walmart Dataset by M Yasser H, Kaggle (CC0: Public Domain)
         https://www.kaggle.com/datasets/yasserh/walmart-dataset

The CSV is not part of this repository. Download it from Kaggle, then put Walmart.csv
(or the zip Kaggle gives you) in demo/data/, or pass its path:

    python demo/load_walmart.py                           # looks in demo/data/
    python demo/load_walmart.py "C:/Downloads/archive.zip"

The connection comes from .env (DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD).
Safe to run again: the table is emptied and reloaded in a single transaction.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import db  # noqa: E402  (needs PROJECT_DIR on sys.path)
from config import Settings, load_settings  # noqa: E402

DATASET_URL = "https://www.kaggle.com/datasets/yasserh/walmart-dataset"
CREDIT = f"Walmart Dataset by M Yasser H (Kaggle): {DATASET_URL}"
DATA_DIR = PROJECT_DIR / "demo" / "data"
CREATE_SQL = PROJECT_DIR / "demo" / "create_walmart_sales.sql"
TABLE = "kaggle.walmart_sales"
COLUMNS = ("store", "date", "weekly_sales", "holiday_flag", "temperature", "fuel_price", "cpi", "unemployment")
CSV_HEADER = ("Store", "Date", "Weekly_Sales", "Holiday_Flag", "Temperature", "Fuel_Price", "CPI", "Unemployment")
EXPECTED = ("2010-02-05", "2012-10-26", 6435)  # first week, last week and row count of the Kaggle file


def find_source(arg: str | None) -> Path:
    """The file given on the command line, else Walmart.csv or a zip in demo/data/."""
    if arg:
        path = Path(arg).expanduser()
        if not path.is_file():
            raise SystemExit(f"File not found: {path}")
        return path
    for path in (DATA_DIR / "Walmart.csv", *sorted(DATA_DIR.glob("*.zip"))):
        if path.is_file():
            return path
    raise SystemExit(
        "Walmart.csv not found.\n"
        f"  1. Download it from {DATASET_URL} (free Kaggle account)\n"
        f"  2. Put Walmart.csv, or the zip you downloaded, in {DATA_DIR}\n"
        "  3. Run this script again"
    )


def read_csv_bytes(path: Path) -> bytes:
    """CSV content of the file; the zip downloaded from Kaggle is read directly."""
    if path.suffix.lower() != ".zip":
        return path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        csv_names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        walmart = [n for n in csv_names if Path(n).name.lower() == "walmart.csv"]
        if not walmart and len(csv_names) != 1:
            raise SystemExit(f"No Walmart.csv inside {path.name} (found: {', '.join(csv_names) or 'no CSV files'})")
        return archive.read((walmart or csv_names)[0])


def check_header(data: bytes) -> None:
    """Refuse files that are not the Kaggle Walmart.csv, before touching the database."""
    lines = data.decode("utf-8-sig", errors="replace").splitlines()
    header = tuple(c.strip().strip('"') for c in lines[0].split(",")) if lines else ()
    if tuple(c.lower() for c in header) != tuple(c.lower() for c in CSV_HEADER):
        raise SystemExit(
            f"Unexpected columns: {', '.join(header) or '(empty file)'}\n"
            f"Expected: {', '.join(CSV_HEADER)}, as in the Walmart.csv from {DATASET_URL}"
        )


def load(cfg: Settings, data: bytes) -> tuple[str, str, int]:
    """Create the table if needed and replace its rows with the CSV content."""
    try:
        import psycopg2
    except ImportError:
        raise SystemExit("The psycopg2 library is not installed. Run setup.bat (or ./setup.sh) first.") from None
    try:
        conn = psycopg2.connect(**db.postgres_params(cfg))
    except psycopg2.Error as e:
        raise SystemExit(f"Could not connect to the database: {str(e).strip()}\n"
                         f"{db.connection_hint(str(e))}") from None
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_SQL.read_text(encoding="utf-8"))
            cur.execute(f"TRUNCATE {TABLE}")
            # The CSV writes dates as DD-MM-YYYY (05-02-2010 = 5 February 2010), but PostgreSQL
            # reads MM-DD by default and would silently store 2 May. Same fix as choosing
            # "dd-MM-yyyy" in DBeaver's import wizard.
            cur.execute("SET LOCAL datestyle TO 'ISO, DMY'")
            cur.copy_expert(f"COPY {TABLE} ({', '.join(COLUMNS)}) FROM STDIN WITH (FORMAT csv, HEADER true)",
                            io.BytesIO(data))
            cur.execute(f"SELECT MIN(date), MAX(date), COUNT(*) FROM {TABLE}")
            first, last, rows = cur.fetchone()
        conn.commit()
    except psycopg2.Error as e:
        raise SystemExit(f"Loading failed: {str(e).strip()}") from None
    finally:
        conn.close()
    return str(first), str(last), rows


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cfg = load_settings()
    if cfg.db_type != "postgres":
        raise SystemExit("The demo loader only supports PostgreSQL (DB_TYPE=postgres in .env).")
    source = find_source(args[0] if args else None)
    data = read_csv_bytes(source)
    check_header(data)

    print(f"Loading {source.name} into {TABLE} ({cfg.database} on {cfg.host}:{cfg.port}) ...")
    print(f"Data: {CREDIT}")
    first, last, rows = load(cfg, data)
    print(f"Loaded {rows:,} rows, {first} to {last}.")
    if (first, last, rows) != EXPECTED:
        print(f"Warning: the Kaggle file has {EXPECTED[2]:,} rows from {EXPECTED[0]} to {EXPECTED[1]}. "
              "Check that this is the right file.")
        return 1
    print("Next: run test_connection.bat, then run.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
