"""
Database connections: PostgreSQL (psycopg2) or Vertica (vertica-python), selected with DB_TYPE.

main.py does not need to know which driver is in use:

    with db.connection(cfg) as conn:
        cur = db.execute(conn, cfg, sql)
        rows = cur.fetchmany(cfg.fetch_size)

Driver errors are translated into QueryFailed (the server rejected the query) or
ConnectionFailed (could not connect, or the connection dropped). Drivers are imported only
when they are used, so PostgreSQL users do not need vertica-python, and vice versa.
"""
from __future__ import annotations

import contextlib
import importlib
import itertools
import re
import socket
import ssl
import sys
from typing import Any, Iterator

from config import Settings, resolve_path

_STREAMABLE = {"select", "with", "values", "table"}
_FIRST_WORD = re.compile(r"[A-Za-z]+")
_INSTALL_COMMAND = {"postgres": "setup.bat (or ./setup.sh)", "vertica": "setup.bat vertica (or ./setup.sh vertica)"}
_cursor_ids = itertools.count(1)


class QueryFailed(Exception):
    """The server rejected the query: syntax error, missing table, no permission, timeout, etc."""


class ConnectionFailed(Exception):
    """Could not connect to the server, or the connection dropped midway."""


# ── PostgreSQL ───────────────────────────────────────────────────────────────

def postgres_params(cfg: Settings) -> dict:
    """Arguments for psycopg2.connect()."""
    options = []
    if cfg.read_timeout > 0:
        options.append(f"-c statement_timeout={cfg.read_timeout * 1000}")  # milliseconds
    if cfg.schema:
        options.append(f"-c search_path={cfg.schema},public")
    params = {
        "host": cfg.host,
        "port": cfg.port,
        "dbname": cfg.database,
        "user": cfg.user,
        "password": cfg.password,
        "connect_timeout": cfg.connection_timeout,
        "application_name": cfg.session_label,
        "sslmode": cfg.ssl_mode,
    }
    if options:
        params["options"] = " ".join(options)
    if cfg.ssl_ca:
        params["sslrootcert"] = str(resolve_path(cfg.ssl_ca))
    return params


def _connect_postgres(cfg: Settings) -> Any:
    psycopg2 = _driver("psycopg2", cfg)
    conn = psycopg2.connect(**postgres_params(cfg))
    conn.set_session(readonly=True)  # an export must never change data
    return conn


# ── Vertica ──────────────────────────────────────────────────────────────────

def vertica_params(cfg: Settings) -> dict:
    """Arguments for vertica_python.connect(). Same as connection_info() in the old version,
    except for the timeout (see the comment below)."""
    info = {
        "host": cfg.host,
        "port": cfg.port,
        "database": cfg.database,
        "user": cfg.user,
        "password": cfg.password,
        # vertica-python applies connection_timeout to EVERY socket operation, including
        # waiting for query results; the read_timeout option is no longer recognised and is
        # silently ignored. The server is checked first with DB_CONNECTION_TIMEOUT
        # (probe_server), and DB_READ_TIMEOUT is used here, so heavy queries are not cut off
        # after 30 seconds.
        "connection_timeout": cfg.read_timeout or None,
        "session_label": cfg.session_label,
        "unicode_error": "strict",
        "autocommit": True,
    }
    ctx = _vertica_ssl_context(cfg)
    if ctx is not None:
        info["ssl"] = ctx
    else:
        info["tlsmode"] = "disable"
    return info


def _vertica_ssl_context(cfg: Settings) -> ssl.SSLContext | None:
    mode = cfg.ssl_mode.lower()
    if mode in ("", "disable"):
        return None
    ctx = ssl.create_default_context()
    if cfg.ssl_ca:
        ctx.load_verify_locations(cafile=str(resolve_path(cfg.ssl_ca)))
    if mode == "require":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif mode == "verify-ca":
        ctx.check_hostname = False
    elif mode != "verify-full":
        raise SystemExit("[config] DB_SSL_MODE must be disable | require | verify-ca | verify-full")
    return ctx


def probe_server(host: str, port: int, timeout: int) -> None:
    """Quick check that the server is reachable, so a VPN that is off fails fast."""
    try:
        with socket.create_connection((host, port), timeout=timeout or None):
            pass
    except OSError as e:
        raise ConnectionFailed(f"Cannot reach server {host}:{port}: {e}") from e


def _connect_vertica(cfg: Settings) -> Any:
    vertica_python = _driver("vertica_python", cfg)
    probe_server(cfg.host, cfg.port, cfg.connection_timeout)
    conn = vertica_python.connect(**vertica_params(cfg))
    if cfg.schema:
        try:
            conn.cursor().execute(f"SET SEARCH_PATH TO {cfg.schema}, public")
        except Exception:
            _close_quietly(conn)
            raise
    return conn


_CONNECTORS = {"postgres": _connect_postgres, "vertica": _connect_vertica}


# ── Used by main.py ──────────────────────────────────────────────────────────

@contextlib.contextmanager
def connection(cfg: Settings) -> Iterator[Any]:
    """Open a connection, translate driver errors, and always close the connection.

    Note: `with psycopg2.connect() as conn` only ends the transaction and leaves the
    connection open (unlike vertica-python), which is why every connection is opened here.
    """
    try:
        conn = _CONNECTORS[cfg.db_type](cfg)
    except Exception as e:
        failed = _translate(cfg, e, conn=None)
        if failed is None:
            raise
        raise failed from e
    try:
        yield conn
    except Exception as e:
        failed = _translate(cfg, e, conn)
        if failed is None:
            raise
        raise failed from e
    finally:
        _close_quietly(conn)


def execute(conn: Any, cfg: Settings, sql: str) -> Any:
    """Run the query and return a cursor that is ready for fetchmany().

    PostgreSQL: a single SELECT statement runs through a server-side cursor, so a result of
    millions of rows is fetched in FETCH_SIZE batches. Without it, psycopg2 loads the whole
    result into memory before the first row can be read. Vertica already streams in batches.
    """
    if cfg.db_type == "postgres" and is_single_select(sql):
        cur = conn.cursor(name=f"sql_export_{next(_cursor_ids)}")
        cur.itersize = cfg.fetch_size
        cur.execute(sql)
        return _Prefetched(cur, cur.fetchmany(cfg.fetch_size))
    cur = conn.cursor()
    cur.execute(sql)
    return cur


class _Prefetched:
    """A PostgreSQL server-side cursor only knows its column names after the first fetch.
    The first batch is fetched up front, so cursor.description is available right away,
    just like with a regular cursor."""

    def __init__(self, cursor: Any, first_rows: list) -> None:
        self._cursor = cursor
        self._first: list | None = first_rows

    @property
    def description(self) -> Any:
        return self._cursor.description

    def fetchmany(self, size: int) -> list:
        if self._first is not None:
            rows, self._first = self._first, None
            return rows
        return self._cursor.fetchmany(size)

    def close(self) -> None:
        self._cursor.close()


def strip_leading_comments(sql: str) -> str:
    """Remove whitespace and -- / /* */ comments in front of the first statement."""
    text = sql.lstrip()
    while text.startswith(("--", "/*")):
        if text.startswith("--"):
            end = text.find("\n")
            text = "" if end == -1 else text[end + 1:]
        else:
            end = text.find("*/")
            text = "" if end == -1 else text[end + 2:]
        text = text.lstrip()
    return text


def is_single_select(sql: str) -> bool:
    """True for a single SELECT / WITH statement (a trailing ';' is allowed).

    Only such queries can run through a server-side cursor. Anything else (several
    statements, or a ';' inside a string) uses a regular cursor: still correct, just not streamed.
    """
    body = strip_leading_comments(sql).rstrip().rstrip(";").rstrip()
    first_word = _FIRST_WORD.match(body)
    return bool(first_word) and first_word.group(0).lower() in _STREAMABLE and ";" not in body


def connection_hint(message: str) -> str:
    """Short hint for the most common connection errors."""
    text = message.lower()
    if "password" in text or "authentication" in text:
        return "Check DB_USER and DB_PASSWORD in .env."
    if "does not exist" in text:
        return "Check DB_NAME and DB_USER in .env."
    return "Check DB_HOST/DB_PORT, your VPN connection and the firewall."


# ── Internal ─────────────────────────────────────────────────────────────────

def _translate(cfg: Settings, error: Exception, conn: Any) -> Exception | None:
    """Turn a driver error into QueryFailed / ConnectionFailed. None = not a database error."""
    message = str(error).strip() or type(error).__name__
    if cfg.db_type == "postgres":
        psycopg2 = sys.modules.get("psycopg2")
        if psycopg2 is None or not isinstance(error, psycopg2.Error):
            return None
        # psycopg2 marks a dropped connection with conn.closed != 0
        lost = conn is None or bool(getattr(conn, "closed", 0))
        return ConnectionFailed(message) if lost else QueryFailed(message)

    errors = getattr(sys.modules.get("vertica_python"), "errors", None)
    if errors is None:
        return None
    if isinstance(error, errors.ConnectionError):
        return ConnectionFailed(message)
    if conn is None and isinstance(error, getattr(errors, "Error", ())):
        return ConnectionFailed(message)  # any error while logging in = failed to connect
    if isinstance(error, errors.QueryError):
        return QueryFailed(message)
    return None


def _driver(module: str, cfg: Settings) -> Any:
    try:
        return importlib.import_module(module)
    except ImportError:
        raise SystemExit(
            f"[db] The {module} library is not installed. Run: {_INSTALL_COMMAND[cfg.db_type]}"
        ) from None


def _close_quietly(resource: Any) -> None:
    try:
        resource.close()
    except Exception:
        pass
