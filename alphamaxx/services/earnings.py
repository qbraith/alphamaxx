"""NASDAQ earnings-calendar client.

The request path NEVER blocks on the network (the API can hang for tens of
seconds): page renders serve whatever is cached — including stale data — and
missing/stale dates are fetched by a single-flight background thread for the
next render, mirroring services/yf_client.py.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, timedelta

import requests

from alphamaxx.config import settings

log = logging.getLogger(__name__)

_NASDAQ_API = "https://api.nasdaq.com/api/calendar/earnings"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
_CACHE: dict[str, dict] = {}  # {date_str: {"data": [...], "ts": float}}
_fetch_lock = threading.Lock()
_inflight: set[str] = set()  # date_strs currently being fetched in background


def _parse_market_cap(s: str) -> float:
    """Parse "$585,179,640,770" → 585179640770.0"""
    if not s or s == "N/A":
        return 0.0
    return float(s.replace("$", "").replace(",", ""))


def _parse_time(t: str) -> str:
    """Convert NASDAQ time codes to readable labels."""
    if t == "time-pre-market":
        return "Pre-Market"
    if t == "time-after-hours":
        return "After Hours"
    return "—"


def _fetch_earnings_network(dt: str) -> list[dict]:
    """Fetch earnings for a single date (YYYY-MM-DD) from the API and cache it.
    Returns list of dicts sorted by market cap descending."""
    try:
        r = requests.get(f"{_NASDAQ_API}?date={dt}", headers=_HEADERS, timeout=10)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        log.warning("NASDAQ earnings API error for %s: %s", dt, e)
        return []

    rows = raw.get("data", {}).get("rows") or []
    parsed = []
    for row in rows:
        mkt_cap_num = _parse_market_cap(row.get("marketCap", ""))
        parsed.append({
            "symbol": row.get("symbol", ""),
            "name": row.get("name", ""),
            "market_cap": row.get("marketCap", "N/A"),
            "market_cap_num": mkt_cap_num,
            "time": _parse_time(row.get("time", "")),
            "eps_forecast": row.get("epsForecast", "—"),
            "last_year_eps": row.get("lastYearEPS", "—"),
            "num_estimates": row.get("noOfEsts", "—"),
            "fiscal_qtr": row.get("fiscalQuarterEnding", "—"),
            "last_year_date": row.get("lastYearRptDt", "—"),
        })

    parsed.sort(key=lambda x: x["market_cap_num"], reverse=True)
    _CACHE[dt] = {"data": parsed, "ts": time.time()}
    return parsed


def fetch_earnings(dt: str) -> list[dict]:
    """Earnings for one date; serves cache when fresh, otherwise fetches.
    Synchronous — for CLI/background use, not the request path."""
    cached = _CACHE.get(dt)
    if cached and time.time() - cached["ts"] < settings.EARNINGS_TTL_S:
        return cached["data"]
    return _fetch_earnings_network(dt)


def _fetch_dates_background(dates: list[str]) -> None:
    try:
        for dt in dates:
            _fetch_earnings_network(dt)
        log.info("Background earnings fetch finished for %d dates", len(dates))
    finally:
        with _fetch_lock:
            _inflight.difference_update(dates)


def fetch_earnings_two_weeks() -> tuple[list[tuple[str, str, str, list[dict]]], bool]:
    """Earnings for the current and next week (Mon-Fri each), from cache only.

    Returns (rows, refreshing): rows are (date_str, week_label, day_name,
    earnings) tuples served from cache (stale data is served as-is); dates that
    are missing or past TTL are queued to one background fetch thread, and
    `refreshing` is True while any date is still being (re)fetched.
    """
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    next_monday = this_monday + timedelta(weeks=1)
    now = time.time()

    result = []
    stale: list[str] = []
    for week_start, week_label in [(this_monday, "This Week"), (next_monday, "Next Week")]:
        for i in range(5):
            d = week_start + timedelta(days=i)
            d_str = d.isoformat()
            day_name = d.strftime("%A, %B %-d")
            cached = _CACHE.get(d_str)
            if not cached or now - cached["ts"] > settings.EARNINGS_TTL_S:
                stale.append(d_str)
            result.append((d_str, week_label, day_name, cached["data"] if cached else []))

    refreshing = False
    if stale:
        with _fetch_lock:
            to_fetch = [d for d in stale if d not in _inflight]
            _inflight.update(to_fetch)
            refreshing = bool(_inflight)
        if to_fetch:
            threading.Thread(
                target=_fetch_dates_background,
                args=(to_fetch,),
                daemon=True,
                name="nasdaq-earnings",
            ).start()
    return result, refreshing


def get_today_str() -> str:
    return date.today().isoformat()
