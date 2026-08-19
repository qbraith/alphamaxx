"""Economic-calendar persistence. The ONLY place econ SQL lives.

Two append-only tables (see data/schema.py): `econ_events` holds the macro
events; `econ_descriptions` holds their tiered seed/AI descriptions. The
*current* description for an event is the row with the latest generated_at.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from alphamaxx.data.db import get_conn, serialized_write

_EVENT_COLS = [
    "event_id", "source", "external_id", "title", "event_type", "importance",
    "event_time", "forecast", "previous", "actual", "updated_at",
]
_DESC_COLS = ["desc_id", "event_id", "body", "tier", "generated_by", "generated_at"]


def _eid(source: str, external_id: str) -> str:
    # Compatibility identifier only, never a signature/password hash.
    return hashlib.sha1(
        f"{source}|{external_id}".encode(), usedforsecurity=False,
    ).hexdigest()


@serialized_write
def upsert_econ_event(ev: dict) -> str:
    """Insert/update one event keyed on its deterministic id. Returns event_id."""
    event_id = _eid(ev["source"], ev["external_id"])
    con = get_conn()
    con.execute(
        """
        INSERT INTO econ_events
            (event_id, source, external_id, title, event_type, importance,
             event_time, forecast, previous, actual, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (event_id) DO UPDATE SET
            title=excluded.title, event_type=excluded.event_type,
            importance=excluded.importance, event_time=excluded.event_time,
            forecast=excluded.forecast, previous=excluded.previous,
            actual=excluded.actual, updated_at=now()
        """,
        [event_id, ev["source"], ev["external_id"], ev["title"], ev.get("event_type"),
         ev.get("importance", "low"), ev["event_time"], ev.get("forecast"),
         ev.get("previous"), ev.get("actual")],
    )
    return event_id


def get_econ_event(event_id: str) -> dict | None:
    con = get_conn()
    row = con.execute(
        f"SELECT {', '.join(_EVENT_COLS)} FROM econ_events WHERE event_id = ?",
        [event_id],
    ).fetchone()
    return dict(zip(_EVENT_COLS, row)) if row else None


def econ_events_between(start: datetime, end: datetime) -> list[dict]:
    """Events with event_time in [start, end], ordered chronologically."""
    con = get_conn()
    rows = con.execute(
        f"""SELECT {', '.join(_EVENT_COLS)} FROM econ_events
            WHERE event_time >= ? AND event_time <= ?
            ORDER BY event_time""",
        [start, end],
    ).fetchall()
    return [dict(zip(_EVENT_COLS, r)) for r in rows]


def future_econ_events(now: datetime | None = None) -> list[dict]:
    """Events at or after `now` (default: current UTC), ordered chronologically."""
    now = now or datetime.now(timezone.utc)
    con = get_conn()
    rows = con.execute(
        f"""SELECT {', '.join(_EVENT_COLS)} FROM econ_events
            WHERE event_time >= ? ORDER BY event_time""",
        [now],
    ).fetchall()
    return [dict(zip(_EVENT_COLS, r)) for r in rows]


@serialized_write
def add_econ_description(event_id: str, body: str, tier: str,
                         generated_by: str) -> dict:
    """Append a new description row (history is never overwritten). Returns it."""
    generated_at = datetime.now(timezone.utc)
    desc_id = hashlib.sha1(  # compatibility identifier, not cryptography
        f"{event_id}|{tier}|{generated_at.isoformat()}".encode(),
        usedforsecurity=False,
    ).hexdigest()
    con = get_conn()
    con.execute(
        """INSERT INTO econ_descriptions
               (desc_id, event_id, body, tier, generated_by, generated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [desc_id, event_id, body, tier, generated_by, generated_at],
    )
    return {
        "desc_id": desc_id, "event_id": event_id, "body": body, "tier": tier,
        "generated_by": generated_by, "generated_at": generated_at,
    }


def current_econ_description(event_id: str) -> dict | None:
    """The latest description for one event (by generated_at)."""
    con = get_conn()
    row = con.execute(
        f"""SELECT {', '.join(_DESC_COLS)} FROM econ_descriptions
            WHERE event_id = ?
            ORDER BY generated_at DESC LIMIT 1""",
        [event_id],
    ).fetchone()
    return dict(zip(_DESC_COLS, row)) if row else None


def current_econ_descriptions_for(ids: list[str]) -> dict[str, dict]:
    """Bulk-load the current description for many events (avoids N+1).

    Returns {event_id: description_dict} for the latest row per event.
    """
    if not ids:
        return {}
    con = get_conn()
    placeholders = ",".join("?" * len(ids))
    rows = con.execute(
        f"""
        SELECT {', '.join(_DESC_COLS)} FROM (
            SELECT {', '.join(_DESC_COLS)},
                   ROW_NUMBER() OVER (
                       PARTITION BY event_id ORDER BY generated_at DESC
                   ) AS rn
            FROM econ_descriptions
            WHERE event_id IN ({placeholders})
        ) WHERE rn = 1
        """,
        list(ids),
    ).fetchall()
    return {r[1]: dict(zip(_DESC_COLS, r)) for r in rows}


@serialized_write
def clear_econ_data() -> None:
    """Delete all econ events + descriptions. They are fully regenerable (seeds
    re-attach and AI notes regenerate on the next ingest/refresh); use this to
    rebuild after a parsing fix changes stored event times."""
    con = get_conn()
    con.execute("DELETE FROM econ_descriptions")
    con.execute("DELETE FROM econ_events")


def econ_tiers_present(event_id: str) -> set[str]:
    """Which description tiers already exist for an event."""
    con = get_conn()
    rows = con.execute(
        "SELECT DISTINCT tier FROM econ_descriptions WHERE event_id = ?",
        [event_id],
    ).fetchall()
    return {r[0] for r in rows}
