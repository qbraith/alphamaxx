"""Thread-local DuckDB connections + the shared writer lock.

A single DuckDB connection object is not safe for concurrent use across
threads. Within one process, multiple connections to the same database file
share the underlying instance (and committed data), but each carries its own
transaction/cursor state. We therefore hand each thread its own connection —
the uvicorn request workers and the price-updater daemon never collide on a
connection.

They CAN collide on rows: DuckDB's optimistic MVCC raises a
TransactionException when two threads write the same row concurrently (the
price scheduler, the in-app ingestion-queue runner, and the yfinance
valuation refresher all upsert overlapping rows). Data-layer write functions
therefore serialize through `write_section()`; reads never take the lock.
Never hold it across network I/O — fetch first, then lock and write.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from typing import ParamSpec, TypeVar

import duckdb

from alphamaxx.config import ensure_private_path, settings

_local = threading.local()

# Reentrant so composed writers (refresh_ttm → refresh_ttm_history) nest.
WRITE_LOCK = threading.RLock()
P = ParamSpec("P")
R = TypeVar("R")


def get_conn() -> duckdb.DuckDBPyConnection:
    """Return this thread's DuckDB connection (one per thread, per process)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        ensure_private_path(settings.STATE_DIR, directory=True)
        settings.DB_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        conn = duckdb.connect(str(settings.DB_PATH))
        ensure_private_path(settings.DB_PATH, directory=False)
        _local.conn = conn
    return conn


@contextmanager
def write_section():
    """Serialize a write batch against all other in-process writers."""
    with WRITE_LOCK:
        yield get_conn()


def serialized_write(func: Callable[P, R]) -> Callable[P, R]:
    """Run a complete read-modify-write function under the writer lock."""
    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with WRITE_LOCK:
            return func(*args, **kwargs)
    return wrapped
