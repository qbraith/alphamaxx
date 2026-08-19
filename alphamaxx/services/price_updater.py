"""Background market-price updater.

Fetches daily prices for all watchlist/portfolio tickers via yfinance.
Runs as a daemon thread inside the app server:
  - 9:15 AM ET pre-market check
  - every 15 minutes 9:30 AM – 4:00 PM ET
  - 4:01 PM ET official-close update
Weekdays only. Manual run: python -m alphamaxx.services.price_updater
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime

import pytz

from alphamaxx.config import settings
from alphamaxx.data.companies import tracked_companies
from alphamaxx.data.momentum import refresh_momentum
from alphamaxx.data.prices import upsert_price_bar
from alphamaxx.data.valuation import upsert_valuation_cache

log = logging.getLogger(__name__)

ET = pytz.timezone("US/Eastern")
PRE_MARKET_CHECK_MINUTE = settings.PREMARKET_HOUR * 60 + settings.PREMARKET_MINUTE
MARKET_OPEN_MINUTE = 9 * 60 + 30
MARKET_CLOSE_MINUTE = 16 * 60
POST_CLOSE_CHECK_MINUTE = settings.POSTCLOSE_HOUR * 60 + settings.POSTCLOSE_MINUTE
INTRADAY_CHECK_INTERVAL = settings.INTRADAY_INTERVAL_MIN * 60
SCHEDULER_POLL_INTERVAL = 60  # check timing gates once per minute

_last_premarket_date: date | None = None
_last_postclose_date: date | None = None
_last_intraday_at: datetime | None = None
_state_lock = threading.Lock()
_update_lock = threading.Lock()


def _update_prices(reason: str = "scheduled") -> None:
    """
    Refresh the latest weekly OHLC bars for watchlist/portfolio companies.

    The prices table stores WEEKLY bars (bulk ingestion uses yfinance
    interval="1wk", labeled by the week's Monday). Upserting the same 1wk
    interval here replaces the current partial week's bar in place — and
    finalizes last week's — instead of appending daily rows, which would mix
    granularities and skew every bar-count window (SMA-50/200, RSI-14).
    Yahoo's Close/Adj Close use the regular-session close, so the 4:01 PM ET
    run records the official close rather than after-hours ticks.
    """
    import pandas as pd
    import yfinance as yf

    if not _update_lock.acquire(blocking=False):
        log.info("Skipping %s update: a price update is already running.", reason)
        return

    try:
        _update_prices_unlocked(reason, yf, pd)
    finally:
        _update_lock.release()


def _update_prices_unlocked(reason: str, yf, pd) -> None:
    companies = tracked_companies()

    if not companies:
        return

    log.info("Updating prices for %d tickers (%s)...", len(companies), reason)

    for permno, ticker in companies:
        _update_company_price_unlocked(permno, ticker, yf, pd)

    log.info("Price update complete (%s).", reason)


def _update_company_price_unlocked(permno: int, ticker: str, yf, pd) -> bool:
    """Fetch one ticker from Yahoo and refresh its weekly bar + momentum.

    This path is deliberately WRDS-independent so locally known ETFs can be
    priced even when they have no Compustat fundamentals.
    """
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="1mo", interval="1wk", auto_adjust=False)

        if hist.empty:
            log.warning("No data for %s", ticker)
            return False

        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
        hist.columns = [c.lower().replace(" ", "_") for c in hist.columns]

        # Upsert the last two weekly bars: finalizes last week and replaces the
        # current partial week without mixing daily bars into weekly history.
        wrote = 0
        for idx, bar in hist.tail(2).iterrows():
            price_date = idx.date() if hasattr(idx, "date") else idx
            if pd.isna(bar.get("close")) or pd.isna(bar.get("adj_close")):
                log.warning(
                    "Skipping %s: NaN close from yfinance (%s)",
                    ticker, price_date,
                )
                continue
            upsert_price_bar(
                permno, price_date,
                bar.get("open"), bar.get("high"),
                bar.get("low"), bar.get("close"),
                bar.get("adj_close"),
                int(bar.get("volume") or 0),
            )
            wrote += 1
        if not wrote:
            return False

        refresh_momentum(permno)
        latest = hist.iloc[-1]
        price_date = hist.index[-1]
        if hasattr(price_date, "date"):
            price_date = price_date.date()

        # Valuations remain best-effort; ETFs commonly omit these fields.
        try:
            info = tk.info
            pe = info.get("trailingPE")
            peg = info.get("pegRatio")
            fpe = info.get("forwardPE")
            pe = round(float(pe), 1) if pe is not None else None
            peg = round(float(peg), 2) if peg is not None else None
            fpe = round(float(fpe), 1) if fpe is not None else None
            upsert_valuation_cache(ticker, pe, peg, fpe)
        except Exception as exc:
            log.warning("PE/PEG refresh failed for %s: %s", ticker, exc)

        log.info("%s: $%.2f (%s)", ticker, latest.get("close", 0), price_date)
        return True
    except Exception:
        log.exception("Error updating %s", ticker)
        return False


def refresh_ticker_price(permno: int, ticker: str, reason: str = "manual") -> bool:
    """Synchronously refresh one local ticker without any WRDS dependency."""
    import pandas as pd
    import yfinance as yf

    if not _update_lock.acquire(timeout=120):
        log.warning("Timed out waiting to refresh %s (%s).", ticker, reason)
        return False
    try:
        log.info("Refreshing %s price (%s)...", ticker, reason)
        return _update_company_price_unlocked(permno, ticker, yf, pd)
    finally:
        _update_lock.release()


def start_ticker_price_refresh(
    permno: int,
    ticker: str,
    reason: str = "portfolio add",
) -> None:
    """Start a non-blocking one-ticker Yahoo refresh for a UI mutation."""
    thread = threading.Thread(
        target=refresh_ticker_price,
        args=(int(permno), str(ticker).strip().upper(), reason),
        daemon=True,
        name=f"price-refresh-{str(ticker).strip().upper()}",
    )
    thread.start()


def _minute_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5


def _mark_premarket_due(now_et: datetime) -> bool:
    global _last_premarket_date
    minute = _minute_of_day(now_et)
    today = now_et.date()
    if PRE_MARKET_CHECK_MINUTE <= minute < MARKET_OPEN_MINUTE and _last_premarket_date != today:
        _last_premarket_date = today
        return True
    return False


def _mark_intraday_due(now_et: datetime) -> bool:
    global _last_intraday_at
    minute = _minute_of_day(now_et)
    if not (MARKET_OPEN_MINUTE <= minute < MARKET_CLOSE_MINUTE):
        return False
    if _last_intraday_at is None or (now_et - _last_intraday_at).total_seconds() >= INTRADAY_CHECK_INTERVAL:
        _last_intraday_at = now_et
        return True
    return False


def _mark_postclose_due(now_et: datetime) -> bool:
    global _last_postclose_date
    minute = _minute_of_day(now_et)
    today = now_et.date()
    if minute >= POST_CLOSE_CHECK_MINUTE and _last_postclose_date != today:
        _last_postclose_date = today
        return True
    return False




def _scheduler_tick(now_et: datetime) -> tuple[str | None, None]:
    """Evaluate and execute one scheduler tick."""
    if not _is_weekday(now_et):
        return None, None

    weekly_reason = None
    with _state_lock:
        if _mark_premarket_due(now_et):
            weekly_reason = "pre-market 9:15 ET check"
        elif _mark_intraday_due(now_et):
            weekly_reason = "intraday 15-minute market-hours check"
        elif _mark_postclose_due(now_et):
            weekly_reason = "post-close 4:01 ET final close update"
    if weekly_reason:
        log.info("%s at %s", weekly_reason, now_et.strftime("%Y-%m-%d %H:%M ET"))
        _update_prices(weekly_reason)
    return weekly_reason, None


def _scheduler_loop() -> None:
    """Run pre-market, intraday, and post-close price update windows."""
    while True:
        try:
            now_et = datetime.now(ET)

            _scheduler_tick(now_et)

        except Exception:
            log.exception("Scheduler error")
        finally:
            time.sleep(SCHEDULER_POLL_INTERVAL)


def start_daily_updater() -> None:
    """Start the background price update scheduler. Call once from app startup."""
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    log.info("Background price updater started")


def run_now() -> None:
    """Manually trigger a price update (for testing or CLI use)."""
    _update_prices("manual run")


if __name__ == "__main__":
    import sys

    import duckdb

    from alphamaxx.log import configure_logging
    configure_logging()
    try:
        run_now()
    except duckdb.IOException as e:
        sys.exit(f"Cannot open alphamaxx.db ({e}).\n"
                 "The app server is probably running — stop it first "
                 "(DuckDB allows one writer process), or use the in-app updater.")
