"""yfinance lookups that NEVER block a request thread.

Yahoo calls can hang for tens of seconds (rate limiting, network), so the
request path only ever reads caches and returns immediately; cache misses
and TTL refreshes are handed to single-flight daemon threads that fill the
caches for the next render.
"""

from __future__ import annotations

import logging
import threading
import time

from alphamaxx.config import settings
from alphamaxx.data.valuation import get_valuation_cache, upsert_valuation_cache

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PE/PEG valuations
# ---------------------------------------------------------------------------

_YF_CACHE: dict[str, dict] = {}  # {ticker: {"pe": float|None, "peg": float|None, "forward_pe": float|None, "ts": float}}
_fetch_lock = threading.Lock()
_inflight: set[str] = set()      # tickers currently being fetched in background


def _fetch_valuations_background(tickers: list[str]) -> None:
    import yfinance as yf
    try:
        for t in tickers:
            try:
                info = yf.Ticker(t).info
                pe = info.get("trailingPE")
                peg = info.get("pegRatio")
                fpe = info.get("forwardPE")
                pe = round(float(pe), 1) if pe is not None else None
                peg = round(float(peg), 2) if peg is not None else None
                fpe = round(float(fpe), 1) if fpe is not None else None
            except Exception as e:
                log.warning("yfinance valuation fetch failed for %s: %s", t, e)
                pe, peg, fpe = None, None, None
            _YF_CACHE[t] = {"pe": pe, "peg": peg, "forward_pe": fpe, "ts": time.time()}
            upsert_valuation_cache(t, pe, peg, fpe)
        log.info("Background valuation fetch finished for %d tickers", len(tickers))
    finally:
        with _fetch_lock:
            _inflight.difference_update(tickers)


def fetch_yf_valuations(tickers: list[str]) -> dict[str, dict]:
    """
    Return {ticker: {'pe', 'peg'}} from cache, immediately.

    Reads the durable `valuation_cache` table (populated by the price-updater
    daemon) plus the in-process cache. Nothing is fetched synchronously:
    misses are queued to a background thread and absent from the result
    (rendered as em-dashes) until the next page view; rows past the TTL are
    served stale AND queued for a background refresh — otherwise PE/PEG could
    stay frozen indefinitely whenever the price updater isn't running.
    """
    durable = get_valuation_cache(tickers)
    now = time.time()
    result: dict[str, dict] = {}
    missing = []
    for t in tickers:
        row = durable.get(t)
        if row is not None:
            result[t] = {"pe": row["pe"], "peg": row["peg"], "forward_pe": row["forward_pe"]}
            if (row.get("age_s") or 0) < settings.YF_TTL_S:
                continue
            missing.append(t)  # stale: served above, refresh in background
            continue
        cached = _YF_CACHE.get(t)
        if cached and now - cached["ts"] < settings.YF_TTL_S:
            result[t] = cached
        else:
            missing.append(t)

    if missing:
        with _fetch_lock:
            to_fetch = [t for t in missing if t not in _inflight]
            _inflight.update(to_fetch)
        if to_fetch:
            threading.Thread(
                target=_fetch_valuations_background,
                args=(to_fetch,),
                daemon=True,
                name="yf-valuations",
            ).start()
    return result


# ---------------------------------------------------------------------------
# Market index strip
# ---------------------------------------------------------------------------

_INDEX_FALLBACK = [
    {"name": "DOW", "pct": 0.0},
    {"name": "S&P 500", "pct": 0.0},
    {"name": "NASDAQ", "pct": 0.0},
]
_INDEX_CACHE: dict = {"data": None, "ts": 0.0}
_index_lock = threading.Lock()
_index_refreshing = False


def _fetch_index_data() -> list[dict]:
    """Fetch current index values via yfinance. Returns static fallback on error."""
    try:
        import yfinance as yf
        indices = {"^DJI": "DOW", "^GSPC": "S&P 500", "^IXIC": "NASDAQ"}
        result = []
        for symbol, name in indices.items():
            t = yf.Ticker(symbol)
            hist = t.history(period="2d")
            if len(hist) >= 2:
                prev = hist["Close"].iloc[-2]
                curr = hist["Close"].iloc[-1]
                pct = (curr - prev) / prev * 100
                if pct != pct:  # NaN from missing closes
                    pct = 0.0
                result.append({"name": name, "pct": round(pct, 2)})
            elif len(hist) == 1:
                result.append({"name": name, "pct": 0.0})
        return result or _INDEX_FALLBACK
    except Exception as e:
        log.warning("Index fetch failed: %s", e)
        return _INDEX_FALLBACK


def _refresh_indices_background() -> None:
    global _index_refreshing
    try:
        data = _fetch_index_data()
        _INDEX_CACHE["data"] = data
        _INDEX_CACHE["ts"] = time.time()
    finally:
        with _index_lock:
            _index_refreshing = False


def get_indices() -> list[dict]:
    """Return cached index data immediately; refresh in the background.

    Never blocks the request thread: the first-ever call serves the static
    fallback while a daemon thread does the real fetch.
    """
    global _index_refreshing
    stale = _INDEX_CACHE["data"] is None or time.time() - _INDEX_CACHE["ts"] > settings.INDEX_TTL_S
    if stale:
        with _index_lock:
            if not _index_refreshing:
                _index_refreshing = True
                threading.Thread(
                    target=_refresh_indices_background,
                    daemon=True,
                    name="yf-indices",
                ).start()
    return _INDEX_CACHE["data"] or _INDEX_FALLBACK
