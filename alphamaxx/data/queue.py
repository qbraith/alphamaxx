"""Durable ingestion queue. Ticker-first: manual entries may not have a local
PERMNO yet; the ingestion worker resolves them against CRSP before download."""

from __future__ import annotations

import re

from alphamaxx.data.db import get_conn, serialized_write

_QUEUE_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")


def _normalize_queue_ticker(ticker: str) -> str:
    """Normalize user-entered ticker symbols while preserving class suffixes."""
    normalized = (ticker or "").strip().upper().replace(" ", "")
    if not normalized:
        return ""
    if not _QUEUE_TICKER_RE.match(normalized):
        return ""
    return normalized


@serialized_write
def enqueue_ingestion(permno: int, reason: str = "manual") -> bool:
    """Queue one company for data ingestion. Returns True if the queue was changed."""
    con = get_conn()
    row = con.execute(
        "SELECT ticker, name FROM companies WHERE permno = ?", [permno]
    ).fetchone()
    if not row:
        return False
    ticker, name = row
    existing = con.execute("""
        SELECT status FROM ingestion_queue
        WHERE status IN ('queued', 'running')
          AND (permno = ? OR UPPER(ticker) = UPPER(?))
        LIMIT 1
    """, [permno, ticker]).fetchone()
    if existing:
        return False
    con.execute("""
        INSERT INTO ingestion_queue
            (permno, ticker, requested_ticker, name, reason, status,
             queued_at, started_at, completed_at, error)
        VALUES (?, ?, ?, ?, ?, 'queued', CURRENT_TIMESTAMP, NULL, NULL, NULL)
    """, [permno, ticker, ticker, name, reason])
    return True


@serialized_write
def enqueue_ticker(ticker: str, reason: str = "manual") -> tuple[bool, str]:
    """Queue a requested ticker without requiring it to exist locally yet."""
    ticker = _normalize_queue_ticker(ticker)
    if not ticker:
        return False, "Please enter a valid US ticker."
    con = get_conn()
    existing = con.execute("""
        SELECT status FROM ingestion_queue
        WHERE UPPER(COALESCE(requested_ticker, ticker)) = ?
          AND status IN ('queued', 'running')
        LIMIT 1
    """, [ticker]).fetchone()
    if existing:
        return False, f"{ticker} is already queued or running."
    con.execute("""
        INSERT INTO ingestion_queue
            (permno, ticker, requested_ticker, name, reason, status,
             queued_at, started_at, completed_at, error)
        VALUES (NULL, ?, ?, NULL, ?, 'queued', CURRENT_TIMESTAMP, NULL, NULL, NULL)
    """, [ticker, ticker, reason])
    return True, f"Queued {ticker} for data download."


def enqueue_missing_ingestion(limit: int | None = None) -> int:
    """Queue pending and partial companies that need ingestion."""
    con = get_conn()
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    rows = con.execute(f"""
        SELECT permno
        FROM companies
        WHERE ingestion_status IN ('pending', 'partial')
        ORDER BY
            CASE ingestion_status WHEN 'partial' THEN 0 ELSE 1 END,
            ticker
        {limit_clause}
    """).fetchall()
    added = 0
    for (permno,) in rows:
        if enqueue_ingestion(permno, "missing-data"):
            added += 1
    return added


def get_ingestion_queue(limit: int = 100) -> list[dict]:
    """Return recent queue items."""
    con = get_conn()
    rows = con.execute("""
        SELECT q.permno, q.ticker, COALESCE(q.name, c.name, '') AS name,
               q.requested_ticker, q.reason, q.status,
               q.queued_at, q.started_at, q.completed_at, q.error,
               c.ingestion_status,
               COALESCE(c.fundamentals_count, 0) AS fundamentals_count,
               COALESCE(c.prices_count, 0) AS prices_count
        FROM ingestion_queue q
        LEFT JOIN companies c ON c.permno = q.permno
        ORDER BY
            CASE q.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'failed' THEN 2 ELSE 3 END,
            q.queued_at DESC
        LIMIT ?
    """, [limit]).fetchall()
    cols = [
        "permno", "ticker", "name", "requested_ticker", "reason", "status", "queued_at",
        "started_at", "completed_at", "error", "ingestion_status",
        "fundamentals_count", "prices_count"
    ]
    return [dict(zip(cols, r)) for r in rows]


def count_pending_queue() -> int:
    """Number of queued or running ingestion items (for the status bar)."""
    con = get_conn()
    return con.execute(
        "SELECT COUNT(*) FROM ingestion_queue WHERE status IN ('queued', 'running')"
    ).fetchone()[0]


def get_queued_ingestions(limit: int | None = None) -> list[dict]:
    """Return queued items for the ingestion worker."""
    con = get_conn()
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    rows = con.execute(f"""
        SELECT permno, ticker, requested_ticker, name
        FROM ingestion_queue
        WHERE status = 'queued'
        ORDER BY queued_at ASC
        {limit_clause}
    """).fetchall()
    return [{"permno": r[0], "ticker": r[1], "requested_ticker": r[2], "name": r[3]} for r in rows]


@serialized_write
def attach_ingestion_queue_company(
    requested_ticker: str,
    permno: int,
    resolved_ticker: str,
    name: str | None = None,
) -> None:
    """Attach a WRDS-resolved company identity to active ticker-first queue rows."""
    con = get_conn()
    requested = _normalize_queue_ticker(requested_ticker)
    if not requested:
        return
    con.execute("""
        UPDATE ingestion_queue
        SET permno = ?, ticker = ?, name = COALESCE(?, name)
        WHERE UPPER(COALESCE(requested_ticker, ticker)) = ?
          AND status IN ('queued', 'running')
    """, [permno, resolved_ticker.upper(), name, requested])


@serialized_write
def mark_ingestion_queue_status(
    permno: int | None,
    status: str,
    error: str | None = None,
    ticker: str | None = None,
) -> None:
    """Update queue status for a worker run."""
    con = get_conn()
    clauses = ["status IN ('queued', 'running')"]
    params: list = []
    if permno is not None:
        clauses.append("permno = ?")
        params.append(permno)
    else:
        ticker = _normalize_queue_ticker(ticker or "")
        if not ticker:
            return
        clauses.append("UPPER(COALESCE(requested_ticker, ticker)) = ?")
        params.append(ticker)
    where_clause = " AND ".join(clauses)
    if status == "running":
        con.execute(f"""
            UPDATE ingestion_queue
            SET status = 'running', started_at = CURRENT_TIMESTAMP, error = NULL
            WHERE {where_clause}
        """, params)
    elif status == "complete":
        con.execute(f"""
            UPDATE ingestion_queue
            SET status = 'complete', completed_at = CURRENT_TIMESTAMP, error = NULL
            WHERE {where_clause}
        """, params)
    elif status == "failed":
        con.execute(f"""
            UPDATE ingestion_queue
            SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error = ?
            WHERE {where_clause}
        """, [error or "Unknown error", *params])


@serialized_write
def clear_completed_ingestion_queue() -> int:
    """Remove complete queue rows and return the number removed."""
    con = get_conn()
    count = con.execute("""
        SELECT COUNT(*) FROM ingestion_queue
        WHERE status = 'complete'
    """).fetchone()[0]
    con.execute("DELETE FROM ingestion_queue WHERE status = 'complete'")
    return count


@serialized_write
def relabel_failed_queue_errors() -> int:
    """Set the short, uniform error message on every failed queue row. Returns
    the number of rows updated."""
    con = get_conn()
    count = con.execute(
        "SELECT COUNT(*) FROM ingestion_queue WHERE status = 'failed'"
    ).fetchone()[0]
    con.execute("""
        UPDATE ingestion_queue
        SET error = COALESCE(requested_ticker, ticker) || ' could not be resolved in WRDS'
        WHERE status = 'failed'
    """)
    return int(count)


@serialized_write
def clear_failed_ingestion_queue() -> int:
    """Remove failed queue rows and return the number removed."""
    con = get_conn()
    count = con.execute("""
        SELECT COUNT(*) FROM ingestion_queue
        WHERE status = 'failed'
    """).fetchone()[0]
    con.execute("DELETE FROM ingestion_queue WHERE status = 'failed'")
    return int(count)


@serialized_write
def delete_ingestion_queue_item(ticker: str, queued_at: str) -> int:
    """Delete one queue row by requested ticker and exact queued timestamp."""
    requested = _normalize_queue_ticker(ticker)
    if not requested or not queued_at:
        return 0
    con = get_conn()
    count = con.execute("""
        SELECT COUNT(*)
        FROM ingestion_queue
        WHERE UPPER(COALESCE(requested_ticker, ticker)) = ?
          AND queued_at = CAST(? AS TIMESTAMP)
    """, [requested, queued_at]).fetchone()[0]
    con.execute("""
        DELETE FROM ingestion_queue
        WHERE UPPER(COALESCE(requested_ticker, ticker)) = ?
          AND queued_at = CAST(? AS TIMESTAMP)
    """, [requested, queued_at])
    return int(count)
