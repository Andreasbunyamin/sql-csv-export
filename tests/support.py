"""Test helpers. SQLite stands in for the database server: it speaks the same Python DB-API
as psycopg2 and vertica-python, so the tests need no real server."""
from __future__ import annotations

import dataclasses
import logging
import os
import sqlite3
from pathlib import Path

import db
from config import Settings

ENV_PREFIXES = ("DB_", "VERTICA_", "OUTPUT_", "SQL_FILE", "CSV_", "FETCH_SIZE")


def clear_settings_env() -> None:
    """Remove settings variables from os.environ (call it inside mock.patch.dict)."""
    for key in [k for k in os.environ if k.startswith(ENV_PREFIXES)]:
        del os.environ[key]


def make_settings(tmp: Path, **changes) -> Settings:
    base = Settings(
        db_type="sqlite", host="localhost", port=5432, database=str(tmp / "demo.db"),
        user="demo", password="secret", schema="", connection_timeout=5, read_timeout=900,
        session_label="tests", ssl_mode="disable", ssl_ca="", sql_file=tmp / "sql",
        output_dir=str(tmp / "output" / "${yyyy}" / "${MM}. ${monthName}"),
        output_file_pattern="${scriptFilename} (${timestamp})", output_timestamp_pattern="dd-MM-yyyy",
        output_encoding="utf-8", output_formats=("csv", "xlsx"), on_conflict="autofix",
        csv_delimiter=",", fetch_size=2,
    )
    return dataclasses.replace(base, **changes)


def use_sqlite() -> None:
    db._CONNECTORS["sqlite"] = lambda cfg: sqlite3.connect(cfg.database)


def drop_sqlite() -> None:
    db._CONNECTORS.pop("sqlite", None)


def create_demo_db(path: Path, rows: int = 5) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sales (store INTEGER, week TEXT, amount REAL, note TEXT)")
    conn.executemany("INSERT INTO sales VALUES (?, ?, ?, ?)",
                     [(i, f"2011-01-{i:02d}", i * 1000.5, None if i % 2 else f"n{i}")
                      for i in range(1, rows + 1)])
    conn.commit()
    conn.close()


def reset_logging() -> None:
    logger = logging.getLogger("sql_export")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
