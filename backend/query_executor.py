"""
PHASE 2 - The Python SQL engine.

Even if a malicious statement slipped past the validator, the executor is built so it
could not do damage (defense in depth):

  1. the database is opened read-only (`mode=ro`)           -> writes fail inside SQLite
  2. a SQLite *authorizer* only permits SELECT / column reads / safe functions
  3. a progress handler aborts queries that run too long    -> no runaway queries
  4. results are capped at `max_rows`                       -> no memory blow-ups
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import get_settings

BLOCKED_FUNCTIONS = {
    "load_extension", "readfile", "writefile", "edit", "fts3_tokenizer",
    "zeroblob", "randomblob",
}


class QueryExecutionError(Exception):
    """Raised when a query cannot be executed (syntax error, timeout, denied, ...)."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False
    elapsed_ms: float = 0.0


def _authorizer(action: int, arg1: str | None, arg2: str | None, dbname: str | None, source: str | None) -> int:
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    if action not in allowed:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and (arg1 or "").lower().startswith("sqlite_"):
        return sqlite3.SQLITE_DENY          # no peeking at system tables
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in BLOCKED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def safe_connection(db_path: str | None = None, timeout_s: float | None = None) -> sqlite3.Connection:
    """Read-only connection with authorizer + time limit installed."""
    settings = get_settings()
    path = Path(db_path or settings.db_path).resolve()
    if not path.exists():
        raise QueryExecutionError(f"Database not found at {path}. Run `python database/init_db.py`.")
    limit = settings.query_timeout_s if timeout_s is None else timeout_s

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.set_authorizer(_authorizer)
    deadline = time.monotonic() + limit
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
    return conn


def _jsonable(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    return value


def execute_query(
    sql: str,
    db_path: str | None = None,
    max_rows: int | None = None,
    timeout_s: float | None = None,
) -> QueryResult:
    """Run a (pre-validated) SELECT and return a JSON-friendly result."""
    settings = get_settings()
    cap = settings.max_rows if max_rows is None else max_rows
    start = time.perf_counter()
    conn = safe_connection(db_path, timeout_s)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        fetched = cur.fetchmany(cap + 1)
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            raise QueryExecutionError("Query timed out. Try a more specific question.") from exc
        raise QueryExecutionError(f"SQL error: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise QueryExecutionError(f"SQL error: {exc}") from exc
    finally:
        conn.close()

    truncated = len(fetched) > cap
    rows = [[_jsonable(v) for v in row] for row in fetched[:cap]]
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
    )


def make_dry_run(db_path: str | None = None) -> Callable[[str], list[str]]:
    """
    Build a function that runs `EXPLAIN QUERY PLAN` for a statement. It catches syntax
    errors / unknown columns without executing the query, and returns the plan lines
    (used by the validator for optimization hints).
    """
    def dry_run(sql: str) -> list[str]:
        conn = safe_connection(db_path, timeout_s=2.0)
        try:
            return [row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()]
        except sqlite3.Error as exc:
            raise QueryExecutionError(f"SQL error: {exc}") from exc
        finally:
            conn.close()

    return dry_run
