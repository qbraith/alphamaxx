"""Economic-calendar service: ForexFactory ingest + event classifier + static
seeds + (optional) AI rewrites + a CLI. Ported from the standalone `fincal`
project onto AlphaMaxx's DuckDB layer.

This is the first external-LLM dependency in AlphaMaxx. It is isolated to this
feature, server-side, and fully optional: with no API key for the active engine
the calendar runs on static seeds alone (zero cloud calls) and never crashes.

CLI:
    python -m alphamaxx.services.econ_calendar --ingest          # fetch + upsert + seed
    python -m alphamaxx.services.econ_calendar --refresh         # tiered AI rewrites that are due
    python -m alphamaxx.services.econ_calendar --build --days 30 # force-build AI notes now
"""

from __future__ import annotations

import functools
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

import requests

from alphamaxx.config import settings
from alphamaxx.data.econ import (
    add_econ_description,
    current_econ_description,
    econ_tiers_present,
    future_econ_events,
    get_econ_event,
    upsert_econ_event,
)
from alphamaxx.services.llm_provider import generate_text, provider_name

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Data sources                                                                 #
# --------------------------------------------------------------------------- #
# ForexFactory: the always-on, no-key *near-week* base source. Only the this-week
# feed is still published — the next-week/month variants were retired (all 404).
# It carries real impact labels, so it's preferred for the current week.
THIS_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_HEADERS = {"User-Agent": "alphamaxx/1.0 (personal economic calendar)"}

# Nasdaq: the always-on, no-key *forward* source (same host the earnings calendar
# uses, see services/earnings.py). One date per request → we loop the window. It
# carries no impact field, so importance is derived from the event type.
NASDAQ_ECON = "https://api.nasdaq.com/api/calendar/economicevents"
NASDAQ_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                  "Accept": "application/json"}
NASDAQ_LOOKAHEAD_DAYS = 35
# Nasdaq quirks (verified against ForexFactory): the `gmt` field is really US
# Eastern time, and `?date=D` returns the events that actually occur on D-1.
_ET = ZoneInfo("America/New_York")

# Financial Modeling Prep: legacy optional source. FMP moved the economic calendar
# behind a paid plan (Aug 2025), so a free key returns 402/403 — kept dormant, out
# of the default path; harmless if ALPHAMAXX_FMP_API_KEY is unset.
FMP_CALENDAR = "https://financialmodelingprep.com/api/v3/economic_calendar"
FMP_LOOKAHEAD_DAYS = 45

# Impact label -> our importance level (case-insensitive lookup).
_IMPACT = {"high": "high", "medium": "med", "low": "low", "holiday": "low"}

# When a source carries no impact (Nasdaq), derive importance from the event type.
_IMPORTANCE_BY_TYPE = {
    "cpi": "high", "core_cpi": "high", "pce": "high", "core_pce": "high",
    "nfp": "high", "unemployment": "high", "fomc": "high", "gdp": "high",
    "ppi": "med", "retail_sales": "med", "pmi": "med", "jobless_claims": "med",
    "jolts": "med", "fed_speak": "med", "durable_goods": "med", "housing": "med",
    "sentiment": "med",
}

# Days-before-event thresholds that trigger a scheduled AI description rewrite.
REFRESH_TIERS = {"m1": 30, "w2": 14, "w1": 7}

_FEED_CACHE: dict[str, dict] = {}  # {url: {"data": [...], "ts": float}}


def _fetch_json(url: str, params: dict | None = None,
                headers: dict | None = None) -> Any:
    """GET JSON with a short TTL cache to avoid hammering the host."""
    cache_key = url + "?" + "&".join(f"{k}={v}" for k, v in (params or {}).items())
    cached = _FEED_CACHE.get(cache_key)
    if cached and time.time() - cached["ts"] < settings.ECON_TTL_S:
        return cached["data"]
    resp = requests.get(url, headers=headers or _HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    _FEED_CACHE[cache_key] = {"data": data, "ts": time.time()}
    return data


def _parse_time(raw: str) -> datetime | None:
    """Normalize a feed timestamp to UTC. Handles ISO-with-offset (ForexFactory,
    e.g. '2026-06-26T08:30:00-04:00') and plain 'YYYY-MM-DD HH:MM:SS' (FMP, UTC)."""
    if not raw:
        return None
    raw = raw.strip().replace(" ", "T", 1) if " " in raw and "T" not in raw else raw
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _clean(v) -> str | None:
    """Trim feed values; treat blanks / HTML no-break spaces as missing."""
    if v is None:
        return None
    s = str(v).replace("\xa0", " ").replace("&nbsp;", " ").strip()
    return s or None


def _norm_event(*, source: str, title: str, time_raw: str,
                impact: str | None, forecast, previous, actual) -> dict | None:
    """Shared normalizer → the upsert-ready event dict (or None to skip). When a
    source has no impact label, importance falls back to the event-type map."""
    event_time = _parse_time(time_raw)
    if event_time is None:
        return None
    title = (title or "").strip()
    if not title:
        return None
    event_type = classify(title)
    if impact:
        importance = _IMPACT.get(impact.lower(), "low")
    else:
        importance = _IMPORTANCE_BY_TYPE.get(event_type, "low")
    return {
        "source": source,
        # Stable id for dedupe: title + timestamp (feeds have no persistent id).
        "external_id": f"{title}|{event_time.isoformat()}",
        "title": title,
        "event_type": event_type,
        "importance": importance,
        "event_time": event_time,
        "forecast": _clean(forecast),
        "previous": _clean(previous),
        "actual": _clean(actual),
    }


def _forexfactory_events() -> list[dict[str, Any]]:
    """US macro events from ForexFactory's this-week feed (no key needed).
    ForexFactory has authoritative tz offsets, so it's preferred for the near week;
    it occasionally 429s, so retry a couple times with short backoff before giving
    up (and letting Nasdaq cover the week)."""
    raw = None
    for attempt in range(3):
        try:
            raw = _fetch_json(THIS_WEEK)
            break
        except Exception as exc:
            if attempt < 2:
                log.info("[forexfactory] fetch failed (%s); retrying…", exc)
                time.sleep(2 * (attempt + 1))
            else:
                log.info("[forexfactory] this-week feed unavailable: %s", exc)
                return []
    if raw is None:
        return []
    out = []
    for item in raw:
        if (item.get("country") or "").upper() != "USD":
            continue
        ev = _norm_event(
            source="forexfactory", title=item.get("title", ""),
            time_raw=item.get("date", ""), impact=item.get("impact"),
            forecast=item.get("forecast"), previous=item.get("previous"),
            actual=item.get("actual"),
        )
        if ev:
            out.append(ev)
    return out


_FMP_US = {"US", "USD", "UNITED STATES"}


def _fmp_events() -> list[dict[str, Any]]:
    """US macro events from Financial Modeling Prep (forward coverage). Dormant
    unless ALPHAMAXX_FMP_API_KEY is set; any failure degrades to an empty list."""
    if not settings.FMP_API_KEY:
        return []
    today = datetime.now(timezone.utc).date()
    params = {
        "from": today.isoformat(),
        "to": (today + timedelta(days=FMP_LOOKAHEAD_DAYS)).isoformat(),
        "apikey": settings.FMP_API_KEY,
    }
    try:
        raw = _fetch_json(FMP_CALENDAR, params)
    except Exception as exc:
        log.info("[fmp] economic calendar unavailable: %s", exc)
        return []
    if not isinstance(raw, list):  # FMP returns an {"Error Message": …} dict on bad key
        log.info("[fmp] unexpected response (bad key?): %.120s", raw)
        return []
    out = []
    for item in raw:
        if (item.get("country") or "").upper() not in _FMP_US:
            continue
        ev = _norm_event(
            source="fmp", title=item.get("event", ""),
            time_raw=item.get("date", ""), impact=item.get("impact"),
            forecast=item.get("estimate"), previous=item.get("previous"),
            actual=item.get("actual"),
        )
        if ev:
            out.append(ev)
    return out


def _nasdaq_events(days: int = NASDAQ_LOOKAHEAD_DAYS) -> list[dict[str, Any]]:
    """US macro events from Nasdaq's economic calendar (no key). One date per
    request. Nasdaq's `?date=D` returns events that actually occur on D-1, and its
    `gmt` field is really US Eastern time — so we query D+1 for each target day and
    interpret the clock time as ET. Any per-date failure is skipped, not fatal."""
    today = datetime.now(_ET).date()
    out: list[dict[str, Any]] = []
    for offset in range(days + 1):
        real_day = today + timedelta(days=offset)   # the day we actually want
        query_day = real_day + timedelta(days=1)     # …lives under Nasdaq's D+1 bucket
        try:
            raw = _fetch_json(NASDAQ_ECON, {"date": query_day.isoformat()},
                              headers=NASDAQ_HEADERS)
        except Exception as exc:
            log.info("[nasdaq] %s unavailable: %s", query_day, exc)
            continue
        rows = ((raw or {}).get("data") or {}).get("rows") or []
        for item in rows:
            if (item.get("country") or "").strip() != "United States":
                continue
            gmt = (item.get("gmt") or "").strip() or "00:00"
            # gmt is ET despite the field name; build the aware ET datetime.
            try:
                hh, mm = (int(x) for x in gmt.split(":")[:2])
                et_dt = datetime(real_day.year, real_day.month, real_day.day,
                                 hh, mm, tzinfo=_ET)
            except ValueError:
                continue
            ev = _norm_event(
                source="nasdaq", title=item.get("eventName", ""),
                time_raw=et_dt.isoformat(),  # aware ET → _parse_time converts to UTC
                impact=None,  # Nasdaq has no impact → derived from event type
                forecast=item.get("consensus"), previous=item.get("previous"),
                actual=item.get("actual"),
            )
            # Nasdaq lists everything (rig counts, CFTC positions, auctions…);
            # keep only recognized market-moving macro releases to avoid noise.
            if ev and ev["event_type"] != "generic_macro":
                out.append(ev)
    return out


def near_events() -> list[dict[str, Any]]:
    """The near-week base source (ForexFactory, has real impact labels)."""
    return _forexfactory_events()


def forward_events() -> list[dict[str, Any]]:
    """The forward-coverage source (Nasdaq, no key, month-ahead)."""
    return _nasdaq_events()


def _forward_filtered(near: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Forward events strictly after the last near-week event, so the two sources
    never produce duplicate rows for the same release at the week boundary."""
    fwd = forward_events()
    cutoff = max((e["event_time"] for e in near), default=None)
    if cutoff is not None:
        fwd = [e for e in fwd if e["event_time"] > cutoff]
    return fwd


def fetch_events() -> list[dict[str, Any]]:
    """All normalized US macro events (near week + forward), deduped at the boundary."""
    near = near_events()
    return near + _forward_filtered(near)


def _upsert_events(events: list[dict[str, Any]]) -> dict[str, int]:
    """Upsert events and attach a static seed the first time each is seen."""
    seeded = 0
    for ev in events:
        event_id = upsert_econ_event(ev)
        if "static" not in econ_tiers_present(event_id):
            add_econ_description(
                event_id, seed_text(ev["event_type"]), tier="static",
                generated_by="seed",
            )
            seeded += 1
    return {"events": len(events), "seeded": seeded}


def ingest_near() -> dict[str, int]:
    """Fast, synchronous: just the near-week source (one request)."""
    summary = _upsert_events(near_events())
    log.info("[ingest-near] %s", summary)
    return summary


def ingest_forward() -> dict[str, int]:
    """The forward window (Nasdaq loop) — run in the background, not the request."""
    summary = _upsert_events(_forward_filtered(near_events()))
    log.info("[ingest-forward] %s", summary)
    return summary


def ingest() -> dict[str, int]:
    """Full ingest (near + forward), synchronous — used by the CLI."""
    summary = _upsert_events(fetch_events())
    log.info("[ingest] %s", summary)
    return summary


# --------------------------------------------------------------------------- #
# Classifier + static seeds                                                    #
# --------------------------------------------------------------------------- #
SEEDS_DIR = Path(__file__).resolve().parent / "econ_seeds"

# Ordered (regex -> event_type). First match wins; specific patterns first.
# event_type also names the seed file: econ_seeds/<event_type>.md
_PATTERNS: list[tuple[str, str]] = [
    (r"\bcore\s+pce\b", "core_pce"),
    (r"\bpce\b", "pce"),
    (r"\bcore\s+cpi\b", "core_cpi"),
    (r"\bcpi\b|consumer price", "cpi"),
    (r"\bppi\b|producer price", "ppi"),
    (r"non[- ]?farm|nfp|payrolls?", "nfp"),
    (r"unemployment rate|jobless rate", "unemployment"),
    (r"initial jobless|jobless claims", "jobless_claims"),
    (r"jolts|job openings", "jolts"),
    (r"fomc|fed interest rate|federal funds|rate decision", "fomc"),
    (r"fed chair|powell|fed.*(speak|testimony)|fed minutes", "fed_speak"),
    (r"\bgdp\b|gross domestic", "gdp"),
    (r"retail sales", "retail_sales"),
    (r"ism (manufacturing|services|non-manufacturing)|pmi", "pmi"),
    (r"consumer (confidence|sentiment)|michigan", "sentiment"),
    (r"durable goods", "durable_goods"),
    (r"housing starts|building permits|new home|existing home", "housing"),
]


def classify(title: str) -> str:
    """Return the normalized event_type for a macro event title (or 'generic_macro')."""
    t = title.lower()
    for pattern, etype in _PATTERNS:
        if re.search(pattern, t):
            return etype
    return "generic_macro"


@functools.lru_cache(maxsize=64)
def _load_seed(event_type: str) -> str | None:
    path = SEEDS_DIR / f"{event_type}.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return None


def seed_text(event_type: str | None) -> str:
    """The static description body for an event_type, falling back to generic."""
    text = _load_seed(event_type or "generic_macro")
    if text:
        return text
    return _load_seed("generic_macro") or "Scheduled event. Description pending."


# --------------------------------------------------------------------------- #
# AI engine (provider-agnostic) + prompts                                      #
# --------------------------------------------------------------------------- #
SYSTEM = (
    "You are a markets analyst writing concise, plain-language briefing notes for a "
    "personal economic calendar. You explain why an event matters and how it is "
    "likely to move markets (rates, the US dollar, equities, gold). You are precise, "
    "avoid hype, never give financial advice, and never invent specific data values "
    "you were not given. Keep notes to 4-7 sentences, in markdown."
)

_TIER_FRAMING = {
    "m1": "It is about a month away. Set the scene: what to watch for and why it matters this cycle.",
    "w2": "It is about two weeks away. Sharpen the focus given how the data/Fed picture has evolved.",
    "w1": "It is about a week away. This is the near-term preview: key levels of expectation and the likely market reaction to a beat vs. a miss.",
    "manual": "The user requested a fresh take because of new market news. Update the note to reflect the current context.",
}


def _description_prompt(event: dict, static_body: str, tier: str) -> str:
    framing = _TIER_FRAMING.get(tier, _TIER_FRAMING["w1"])
    forecast, previous = event.get("forecast"), event.get("previous")
    known = []
    if forecast:
        known.append(f"Consensus/forecast: {forecast}")
    if previous:
        known.append(f"Previous: {previous}")
    known_block = "\n".join(known) if known else "No forecast/previous figures available."

    event_time = _to_utc(event["event_time"])
    return f"""Write an updated briefing note for this upcoming event.

Event: {event['title']}
Type: {event.get('event_type') or 'generic_macro'} (macro)
Scheduled: {event_time:%A, %B %d, %Y at %I:%M %p UTC}
Typical importance: {event.get('importance', 'low')}
{known_block}

Background (the static reference note):
\"\"\"
{static_body}
\"\"\"

Task: {framing}

Use your general knowledge of the current macro and market backdrop to make the
note timely and specific to this release in this cycle. Do NOT fabricate exact
data prints or dates. Return only the note body in markdown (no title, no preamble)."""


def _generate(prompt: str, *, system: str, max_tokens: int = 600) -> str:
    """Return generated text from the configured engine. Raises on hard failure
    so callers can fall back to the existing description."""
    return generate_text(prompt, system=system, max_tokens=max_tokens)


def _engine_label() -> str:
    return provider_name()


# --------------------------------------------------------------------------- #
# Generation orchestration                                                     #
# --------------------------------------------------------------------------- #
def _to_utc(value) -> datetime:
    """Coerce a stored event_time (datetime or ISO str) to aware UTC."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _retry_delay(msg: str) -> float:
    """Pull the server-suggested retry delay (e.g. 'retryDelay': '29s') from a
    rate-limit error message; fall back to the per-RPM interval."""
    m = re.search(r"retryDelay'?\s*:?\s*'?(\d+(?:\.\d+)?)s", msg)
    if m:
        return float(m.group(1)) + 1.0
    return _ai_interval()


def _ai_interval() -> float:
    """Minimum seconds between AI calls to stay under the configured RPM."""
    return 60.0 / max(settings.AI_RPM, 1)


def _generate_with_retry(prompt: str, attempts: int = 3) -> str:
    """Call the engine, retrying on rate-limit (429 / RESOURCE_EXHAUSTED)."""
    for i in range(attempts):
        try:
            return _generate(prompt, system=SYSTEM)
        except Exception as exc:
            msg = str(exc)
            rate_limited = "RESOURCE_EXHAUSTED" in msg or "429" in msg
            if rate_limited and i < attempts - 1:
                delay = _retry_delay(msg)
                log.info("[generate] rate-limited; retrying in %.0fs", delay)
                time.sleep(delay)
                continue
            raise


def generate_for_event(event: dict, tier: str) -> dict | None:
    """Generate a fresh AI description for an event at the given tier, store it,
    and return the new row. Returns None on failure (existing rows untouched)."""
    static_body = seed_text(event.get("event_type"))
    prompt = _description_prompt(event, static_body, tier)
    try:
        body = _generate_with_retry(prompt)
    except Exception as exc:
        log.warning("[generate] AI failed for %s (%s): %s",
                    event["event_id"], event["title"], exc)
        return None
    if not body:
        log.warning("[generate] empty AI response for %s", event["event_id"])
        return None
    return add_econ_description(
        event["event_id"], body=body, tier=tier, generated_by=_engine_label()
    )


def _days_until(event_time, now: datetime) -> float:
    return (_to_utc(event_time) - now).total_seconds() / 86400.0


def _due_tier(days_left: float, existing_tiers: set[str]) -> str | None:
    """Highest-priority tier that is due and not yet generated. Tightest window
    first, so an event first seen at 8 days out gets 'w1', not all three."""
    for tier in ("w1", "w2", "m1"):  # 7, 14, 30 — tightest first
        if days_left <= REFRESH_TIERS[tier] and tier not in existing_tiers:
            return tier
    return None


def refresh() -> dict[str, int]:
    """For each future event, generate its one due tier (1mo/2wk/1wk) if missing."""
    now = datetime.now(timezone.utc)
    events = future_econ_events(now)
    interval = _ai_interval()
    generated = skipped = 0
    last_call = 0.0
    for ev in events:
        days_left = _days_until(ev["event_time"], now)
        if days_left < 0:
            continue
        tier = _due_tier(days_left, econ_tiers_present(ev["event_id"]))
        if tier is None:
            skipped += 1
            continue
        # Pace calls to stay under the AI RPM cap (Gemini free tier = 5/min).
        wait = interval - (time.monotonic() - last_call)
        if last_call and wait > 0:
            time.sleep(wait)
        log.info("[refresh] %s '%s' %.1fd -> tier %s",
                 ev["event_id"], ev["title"], days_left, tier)
        last_call = time.monotonic()
        if generate_for_event(ev, tier):
            generated += 1
    summary = {"generated": generated, "skipped": skipped, "considered": len(events)}
    log.info("[refresh] done: %s", summary)
    return summary


_refresh_lock = threading.Lock()


def _forward_and_refresh_in_background() -> None:
    """Fill the forward window (Nasdaq loop, ~10s) then run the tiered AI refresh,
    holding _refresh_lock so concurrent syncs don't stack. Uses this thread's own
    DuckDB connection (committed writes are visible to request threads next load)."""
    try:
        ingest_forward()
        refresh()
    except Exception:  # never let a background failure escape
        log.exception("[sync] background forward-ingest/refresh failed")
    finally:
        _refresh_lock.release()


def sync() -> dict[str, int]:
    """In-app sync (the ⟳ Sync button): ingest this-week events + static seeds
    synchronously (fast), then, in a background daemon thread, fetch the forward
    window and generate due AI notes so the request returns immediately. Repeated
    clicks won't stack — a second click while one is running just skips the spawn."""
    summary = ingest_near()
    if _refresh_lock.acquire(blocking=False):
        threading.Thread(
            target=_forward_and_refresh_in_background, daemon=True, name="econ-refresh",
        ).start()
    else:
        log.info("[sync] refresh already running; skipping background spawn")
    return summary


def update_event(event_id: str) -> dict | None:
    """One manual-tier generation on demand (the 'Update now' button)."""
    event = get_econ_event(event_id)
    if not event:
        raise ValueError(f"Event {event_id} not found")
    return generate_for_event(event, tier="manual")


def build(days: int = 30, limit: int = 0, tier: str = "manual") -> dict[str, int]:
    """Force-build AI descriptions now for events in the next `days` days,
    instead of waiting for the tiered schedule."""
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=days)
    events = [ev for ev in future_econ_events(now)
              if _to_utc(ev["event_time"]) <= window_end]
    log.info("[build] %d event(s) in next %dd (tier=%s)", len(events), days, tier)
    interval = _ai_interval()
    generated = failed = 0
    last_call = 0.0
    for ev in events:
        if limit and generated >= limit:
            log.info("[build] hit limit %d; stopping.", limit)
            break
        wait = interval - (time.monotonic() - last_call)
        if last_call and wait > 0:
            time.sleep(wait)
        last_call = time.monotonic()
        if generate_for_event(ev, tier):
            generated += 1
        else:
            failed += 1
    summary = {"matched": len(events), "generated": generated, "failed": failed}
    log.info("[build] done: %s", summary)
    return summary


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    import argparse

    from alphamaxx.data import init_db
    from alphamaxx.log import configure_logging

    configure_logging()

    p = argparse.ArgumentParser(prog="alphamaxx.services.econ_calendar",
                                description=__doc__)
    p.add_argument("--ingest", action="store_true",
                   help="fetch the weekly feeds, upsert events, attach static seeds")
    p.add_argument("--refresh", action="store_true",
                   help="generate tiered AI rewrites (1mo/2wk/1wk) that are due")
    p.add_argument("--build", action="store_true",
                   help="force-build AI notes now (manual tier) for the next --days")
    p.add_argument("--days", type=int, default=30, help="look-ahead window for --build")
    p.add_argument("--limit", type=int, default=0, help="cap AI calls for --build (0 = no cap)")
    args = p.parse_args(argv)

    init_db()
    if not (args.ingest or args.refresh or args.build):
        p.error("specify at least one of --ingest / --refresh / --build")
    if args.ingest:
        ingest()
    if args.refresh:
        refresh()
    if args.build:
        build(days=args.days, limit=args.limit)


if __name__ == "__main__":
    main()
