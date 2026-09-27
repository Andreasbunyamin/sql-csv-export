"""Reads the settings from the .env file in the project folder."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

DB_TYPES = ("postgres", "vertica")
DB_TYPE_ALIASES = {"postgresql": "postgres", "pg": "postgres"}
DEFAULT_PORTS = {"postgres": 5432, "vertica": 5433}
SSL_MODES = {
    "postgres": ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"),
    "vertica": ("disable", "require", "verify-ca", "verify-full"),
}

# Variable names of the old Vertica-only version are still read, so an old .env needs no edits.
LEGACY_KEYS = {
    "DB_HOST": "VERTICA_HOST",
    "DB_PORT": "VERTICA_PORT",
    "DB_NAME": "VERTICA_DB",
    "DB_USER": "VERTICA_USER",
    "DB_PASSWORD": "VERTICA_PASSWORD",
    "DB_SCHEMA": "VERTICA_SCHEMA",
    "DB_CONNECTION_TIMEOUT": "VERTICA_CONNECTION_TIMEOUT",
    "DB_READ_TIMEOUT": "VERTICA_READ_TIMEOUT",
    "DB_SESSION_LABEL": "VERTICA_SESSION_LABEL",
    "DB_SSL_MODE": "VERTICA_SSL_MODE",
    "DB_SSL_CA": "VERTICA_SSL_CA",
}

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _raw(key: str) -> str | None:
    """Value from the environment. DB_HOST etc. may also be set with the old names VERTICA_HOST etc."""
    value = os.getenv(key)
    legacy = LEGACY_KEYS.get(key)
    if legacy and (value is None or not value.strip()):
        legacy_value = os.getenv(legacy)
        if legacy_value is not None:
            return legacy_value
    return value


def _required(key: str) -> str:
    value = (_raw(key) or "").strip()
    # "ISI_..." placeholders (Indonesian for "FILL_...") from older templates count as empty
    if not value or value.upper().startswith("ISI_"):
        raise SystemExit(f"[config] {key} is not set in .env")
    return value


def _optional(key: str, default: str = "") -> str:
    value = _raw(key)
    return (default if value is None else value).strip()


def _int(key: str, default: int) -> int:
    raw = _optional(key, str(default))
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"[config] {key} must be a number, got: {raw!r}")


def resolve_path(value: str) -> Path:
    """Relative paths start from the project folder, not from the terminal's current folder."""
    p = Path(value).expanduser()
    return p if p.is_absolute() else BASE_DIR / p


@dataclass(frozen=True)
class Settings:
    db_type: str
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)  # kept out of logs and tracebacks
    schema: str
    connection_timeout: int
    read_timeout: int
    session_label: str
    ssl_mode: str
    ssl_ca: str
    sql_file: Path
    output_dir: str
    output_file_pattern: str
    output_timestamp_pattern: str
    output_encoding: str
    output_formats: tuple[str, ...]
    on_conflict: str
    csv_delimiter: str
    fetch_size: int


def _db_type() -> str:
    raw = _optional("DB_TYPE").lower()
    if not raw:
        # An old .env has no DB_TYPE: if it sets VERTICA_HOST, it is a Vertica setup
        return "vertica" if _optional("VERTICA_HOST") else "postgres"
    db_type = DB_TYPE_ALIASES.get(raw, raw)
    if db_type not in DB_TYPES:
        raise SystemExit(f"[config] DB_TYPE must be {' or '.join(DB_TYPES)} (got: {raw!r})")
    return db_type


def _ssl_mode(db_type: str) -> str:
    mode = _optional("DB_SSL_MODE", "disable").lower() or "disable"
    if mode not in SSL_MODES[db_type]:
        raise SystemExit(f"[config] DB_SSL_MODE must be {' | '.join(SSL_MODES[db_type])} (got: {mode!r})")
    return mode


def _encoding() -> str:
    enc = _optional("OUTPUT_ENCODING", "utf-8") or "utf-8"
    try:
        "x".encode(enc)
    except LookupError:
        raise SystemExit(f"[config] Unknown OUTPUT_ENCODING: {enc!r}")
    return enc


def _formats() -> tuple[str, ...]:
    raw = _optional("OUTPUT_FORMATS", "csv,xlsx") or "csv,xlsx"
    formats = tuple(dict.fromkeys(f.strip().lower().lstrip(".") for f in raw.split(",") if f.strip()))
    unknown = [f for f in formats if f not in ("csv", "xlsx")]
    if unknown or not formats:
        raise SystemExit(f"[config] OUTPUT_FORMATS must be csv, xlsx or csv,xlsx (got: {raw!r})")
    return formats


def _conflict() -> str:
    mode = (_optional("OUTPUT_ON_CONFLICT", "autofix") or "autofix").lower()
    if mode not in ("autofix", "overwrite"):
        raise SystemExit("[config] OUTPUT_ON_CONFLICT must be autofix or overwrite")
    return mode


def load_settings(env_file: Path | None = None) -> Settings:
    env_file = env_file or ENV_FILE
    if not env_file.exists():
        raise SystemExit(
            f"[config] No .env file found in {env_file.parent}\n"
            "         Copy .env.example to .env and fill in the values."
        )
    # interpolate=False so that ${scriptFilename} etc. are not read as environment variables.
    # utf-8-sig so that a .env saved by Notepad (with a BOM) is still read correctly.
    load_dotenv(env_file, override=True, interpolate=False, encoding="utf-8-sig")

    db_type = _db_type()
    schema = _optional("DB_SCHEMA")
    if schema and not _IDENT.match(schema):
        raise SystemExit(f"[config] Invalid DB_SCHEMA: {schema!r}")

    delimiter = _optional("CSV_DELIMITER", ",").replace("\\t", "\t") or ","
    if len(delimiter) != 1:
        raise SystemExit("[config] CSV_DELIMITER must be a single character (e.g. , ; | \\t)")

    return Settings(
        db_type=db_type,
        host=_required("DB_HOST"),
        port=_int("DB_PORT", DEFAULT_PORTS[db_type]),
        database=_required("DB_NAME"),
        user=_required("DB_USER"),
        password=_required("DB_PASSWORD"),
        schema=schema,
        connection_timeout=_int("DB_CONNECTION_TIMEOUT", 30),
        read_timeout=_int("DB_READ_TIMEOUT", 900),
        session_label=_optional("DB_SESSION_LABEL", "sql_csv_export"),
        ssl_mode=_ssl_mode(db_type),
        ssl_ca=_optional("DB_SSL_CA"),
        sql_file=resolve_path(_optional("SQL_FILE", "sql")),
        output_dir=_optional("OUTPUT_DIR", "output"),
        output_file_pattern=_optional("OUTPUT_FILE_PATTERN", "${scriptFilename} (${timestamp})"),
        output_timestamp_pattern=_optional("OUTPUT_TIMESTAMP_PATTERN", "dd-MM-yyyy"),
        output_encoding=_encoding(),
        output_formats=_formats(),
        on_conflict=_conflict(),
        csv_delimiter=delimiter,
        fetch_size=_int("FETCH_SIZE", 10000),
    )
