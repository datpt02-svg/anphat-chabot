"""M2 database helpers.

psycopg 3.3 removed `psycopg.extras.execute_values`. We use `cur.executemany`
with the standard INSERT VALUES (%s, %s, ...) pattern, which is internally
optimized (uses the pipeline mode for large batches).

For batch counts (inserted vs skipped/conflict) we use a pre-check SELECT
or before/after counts; see pipeline.py.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterable, Sequence
from typing import Any, Iterator

import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


# Re-exports for convenience.
__all__ = [
    "Jsonb",
    "connect",
    "transaction",
    "execute_many",
    "pg_errors",
]


EXECUTE_VALUES_PAGE_SIZE = 1000  # kept as constant for legacy callers


def connect(
    database_url: str | None = None,
    *,
    autocommit: bool = False,
) -> psycopg.Connection:
    """Open a new connection with dict_row.

    `autocommit=True` commits each statement immediately. Used by cleanup paths
    that must keep running even if a single statement raises.
    """
    if database_url is None:
        from scripts.m2_pipeline.config import get_database_url
        database_url = get_database_url()

    return psycopg.connect(
        database_url,
        autocommit=autocommit,
        row_factory=dict_row,
    )


@contextlib.contextmanager
def transaction(conn: psycopg.Connection) -> Iterator[psycopg.Cursor]:
    """Yield a dict_row cursor inside a transaction. Commits on success."""
    with conn.transaction():
        with conn.cursor() as cur:
            yield cur


def execute_many(
    cur: psycopg.Cursor,
    sql: str,
    rows: Sequence[Sequence[Any]],
) -> None:
    """Bulk execute via `cur.executemany`. Rows must align with %s placeholders.

    `cur.executemany` in psycopg 3 is optimized for batch inserts:
    - For simple INSERT VALUES it uses the pipeline protocol (1 round-trip)
    - It supports ON CONFLICT, RETURNING, etc. via the SQL text.
    - It does NOT return per-row results from RETURNING; for that use
      `cur.execute()` with a CTE pattern.
    """
    if not rows:
        return
    cur.executemany(sql, rows)
