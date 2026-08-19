"""Economic-calendar ingest + persistence tests. Uses the temp DB from conftest;
the network feed is monkeypatched so no outbound request is made."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alphamaxx.data import econ
from alphamaxx.services import econ_calendar


def _fixture_feed(when: datetime) -> list[dict]:
    """One ForexFactory-shaped USD CPI item plus a non-USD item to be filtered."""
    return [
        {
            "title": "CPI m/m",
            "country": "USD",
            "date": when.isoformat(),
            "impact": "High",
            "forecast": "0.3%",
            "previous": "0.2%",
            "actual": "",
        },
        {  # must be filtered out (non-US)
            "title": "German IFO",
            "country": "EUR",
            "date": when.isoformat(),
            "impact": "Medium",
        },
    ]


def test_classify_known_titles():
    assert econ_calendar.classify("CPI m/m") == "cpi"
    assert econ_calendar.classify("Core PCE Price Index m/m") == "core_pce"
    assert econ_calendar.classify("FOMC Statement") == "fomc"
    assert econ_calendar.classify("Some Obscure Release") == "generic_macro"


def test_seed_text_falls_back():
    assert econ_calendar.seed_text("cpi")  # real seed exists
    # unknown type falls back to the generic seed (non-empty)
    assert econ_calendar.seed_text("does_not_exist")


def test_ingest_populates_event_and_static_seed(db, monkeypatch):
    db.execute("DELETE FROM econ_descriptions")
    db.execute("DELETE FROM econ_events")

    when = datetime.now(timezone.utc) + timedelta(days=3)
    # this-week feed carries the item; next-week feed is empty.
    monkeypatch.setattr(
        econ_calendar, "_fetch_json",
        lambda url, params=None, headers=None: (
            _fixture_feed(when) if url == econ_calendar.THIS_WEEK else {}
        ),
    )

    summary = econ_calendar.ingest()
    assert summary["events"] == 1  # only the USD event survives the filter
    assert summary["seeded"] == 1

    events = econ.future_econ_events()
    assert len(events) == 1
    ev = events[0]
    assert ev["title"] == "CPI m/m"
    assert ev["event_type"] == "cpi"
    assert ev["importance"] == "high"

    desc = econ.current_econ_description(ev["event_id"])
    assert desc is not None
    assert desc["tier"] == "static"
    assert desc["generated_by"] == "seed"
    assert desc["body"]  # non-empty seed body

    # Re-ingest is idempotent: no duplicate event, no second static seed.
    again = econ_calendar.ingest()
    assert again["events"] == 1
    assert again["seeded"] == 0
    assert econ.econ_tiers_present(ev["event_id"]) == {"static"}


def test_sync_ingests_and_is_reentrant(db, monkeypatch):
    db.execute("DELETE FROM econ_descriptions")
    db.execute("DELETE FROM econ_events")
    when = datetime.now(timezone.utc) + timedelta(days=4)
    monkeypatch.setattr(
        econ_calendar, "_fetch_json",
        lambda url, params=None, headers=None: (
            _fixture_feed(when) if url == econ_calendar.THIS_WEEK else {}
        ),
    )
    # No AI key in tests → background refresh no-ops gracefully; sync must not raise.
    summary = econ_calendar.sync()
    assert summary["events"] == 1
    # A second immediate sync (possible overlapping refresh) must not error.
    again = econ_calendar.sync()
    assert again["events"] == 1
    assert len(econ.future_econ_events()) == 1


def test_fetch_events_merges_forward_strictly(monkeypatch):
    """Forward (Nasdaq) events at/before the last near-week (ForexFactory) event are
    dropped; only strictly-forward events are kept — no cross-source dupes."""
    base = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    near = [
        {"source": "forexfactory", "event_time": base, "title": "CPI"},
        {"source": "forexfactory", "event_time": base + timedelta(days=2), "title": "NFP"},
    ]
    forward = [
        {"source": "nasdaq", "event_time": base + timedelta(days=1), "title": "Overlap"},    # dropped
        {"source": "nasdaq", "event_time": base + timedelta(days=2), "title": "SameAsLast"},  # dropped (not strictly after)
        {"source": "nasdaq", "event_time": base + timedelta(days=9), "title": "Future"},      # kept
    ]
    monkeypatch.setattr(econ_calendar, "near_events", lambda: near)
    monkeypatch.setattr(econ_calendar, "forward_events", lambda: forward)
    merged = econ_calendar.fetch_events()
    assert [e["title"] for e in merged] == ["CPI", "NFP", "Future"]


def _nasdaq_payload(day_iso: str) -> dict:
    """A Nasdaq economicevents-shaped payload: a US high-impact event, a US
    med-impact event, a US noise event (filtered), and a non-US event (filtered)."""
    return {"data": {"rows": [
        {"gmt": "12:30", "country": "United States", "eventName": "Core CPI m/m",
         "consensus": "0.3%", "previous": "0.2%", "actual": "&nbsp;"},
        {"gmt": "12:30", "country": "United States", "eventName": "Initial Jobless Claims",
         "consensus": "230K", "previous": "225K", "actual": ""},
        {"gmt": "14:00", "country": "United States", "eventName": "Baker Hughes Rig Count",
         "consensus": " ", "previous": "", "actual": ""},
        {"gmt": "01:00", "country": "Japan", "eventName": "Construction Orders",
         "consensus": "", "previous": "", "actual": ""},
    ]}}


def test_nasdaq_events_parse_filter_and_importance(monkeypatch):
    """Nasdaq rows: US-only, recognized macro only (rig-count noise + Japan dropped),
    importance derived from type, blank/&nbsp; values cleaned to None."""
    monkeypatch.setattr(
        econ_calendar, "_fetch_json",
        lambda url, params=None, headers=None: _nasdaq_payload(params["date"]),
    )
    evs = econ_calendar._nasdaq_events(days=0)  # real day = today (queried as today+1)
    by_title = {e["title"]: e for e in evs}
    assert set(by_title) == {"Core CPI m/m", "Initial Jobless Claims"}  # noise + Japan gone
    cpi = by_title["Core CPI m/m"]
    assert cpi["event_type"] == "core_cpi" and cpi["importance"] == "high"
    assert cpi["forecast"] == "0.3%" and cpi["actual"] is None  # &nbsp; cleaned
    assert by_title["Initial Jobless Claims"]["importance"] == "med"
    # gmt is treated as ET on the real day (today), stored as UTC.
    from datetime import datetime as _dt
    today = _dt.now(econ_calendar._ET).date()
    expected = _dt(today.year, today.month, today.day, 12, 30,
                   tzinfo=econ_calendar._ET).astimezone(timezone.utc)
    assert cpi["event_time"] == expected


def test_nasdaq_queries_day_ahead(monkeypatch):
    """Nasdaq's ?date=D holds D-1's events, so we must query real_day + 1."""
    seen = []
    monkeypatch.setattr(
        econ_calendar, "_fetch_json",
        lambda url, params=None, headers=None: (seen.append(params["date"]), {})[1],
    )
    econ_calendar._nasdaq_events(days=0)
    from datetime import datetime as _dt, timedelta as _td
    tomorrow = (_dt.now(econ_calendar._ET).date() + _td(days=1)).isoformat()
    assert seen == [tomorrow]


def test_bulk_descriptions_lookup(db, monkeypatch):
    db.execute("DELETE FROM econ_descriptions")
    db.execute("DELETE FROM econ_events")
    when = datetime.now(timezone.utc) + timedelta(days=5)
    # this-week feed carries the item; next-week feed is empty.
    monkeypatch.setattr(
        econ_calendar, "_fetch_json",
        lambda url, params=None, headers=None: (
            _fixture_feed(when) if url == econ_calendar.THIS_WEEK else {}
        ),
    )
    econ_calendar.ingest()

    ev = econ.future_econ_events()[0]
    bulk = econ.current_econ_descriptions_for([ev["event_id"]])
    assert ev["event_id"] in bulk
    assert bulk[ev["event_id"]]["tier"] == "static"
