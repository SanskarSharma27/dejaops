"""CockroachDB access layer: psycopg3 pool, serialization-retry, vector helpers.

CockroachDB runs SERIALIZABLE by default and surfaces contention as SQLSTATE
40001; the client is expected to retry. `with_retry` wraps any unit of work in
that loop with exponential backoff + jitter.
"""

import atexit
import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

log = logging.getLogger("dejaops.db")

_pool: ConnectionPool | None = None

T = TypeVar("T")

MAX_RETRIES = 5


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        cfg = settings()
        if not cfg.database_url:
            raise RuntimeError("DATABASE_URL is not set")
        # Lambda gets one warm container per concurrent request, so a tiny pool
        # is correct: it exists for connection reuse, not concurrency.
        _pool = ConnectionPool(
            cfg.database_url,
            min_size=0,
            max_size=2,
            timeout=10,  # fail fast: API Gateway caps requests at ~29s
            kwargs={"row_factory": dict_row, "application_name": "dejaops"},
            open=True,
        )
        atexit.register(_pool.close)  # clean shutdown for scripts/tests
    return _pool


def with_retry(fn: Callable[[psycopg.Connection], T]) -> T:
    """Run `fn(conn)` and retry on CockroachDB serialization failures (40001)."""
    last: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with pool().connection() as conn:
                return fn(conn)
        except psycopg.errors.SerializationFailure as exc:
            last = exc
            delay = min(0.05 * (2**attempt) + random.uniform(0, 0.05), 1.0)
            log.warning("serialization retry %d/%d in %.2fs", attempt + 1, MAX_RETRIES, delay)
            time.sleep(delay)
    raise last  # type: ignore[misc]


def query(sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
    def run(conn: psycopg.Connection) -> list[dict[str, Any]]:
        return conn.execute(sql, params).fetchall()

    return with_retry(run)


def query_one(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    def run(conn: psycopg.Connection) -> dict[str, Any] | None:
        return conn.execute(sql, params).fetchone()

    return with_retry(run)


def execute(sql: str, params: tuple | dict | None = None) -> None:
    def run(conn: psycopg.Connection) -> None:
        conn.execute(sql, params)

    with_retry(run)


# --- VECTOR (de)serialization -------------------------------------------------
# CockroachDB's VECTOR type speaks pgvector's text format. Passing vectors as
# text + ::VECTOR cast avoids any dependency on OID registration working
# against CockroachDB's pg_catalog emulation.


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


def from_pgvector(text: str) -> list[float]:
    return [float(x) for x in text.strip("[]").split(",") if x]
