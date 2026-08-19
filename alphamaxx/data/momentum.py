"""Momentum & technical metrics: SMA-50d/200d, RSI, valuation ratios.

Populates the `momentum` table from price + ttm_cache data. Prices are
weekly bars, so the daily-horizon SMAs are approximated by weekly sampling
(200 trading days ≈ SMA_200D_WEEKS weekly closes, 50 ≈ SMA_50D_WEEKS); the
`sma_50`/`sma_200` column names are kept for schema compatibility. RSI is a
14-week Wilder RSI. Pure Python — no pandas dependency at runtime. Called by
the ingestion pipeline and the price-updater daemon after each price update.

Run standalone to refresh everything: python -m alphamaxx.data.momentum
"""

from __future__ import annotations

import logging

from alphamaxx.config import settings
from alphamaxx.data.db import get_conn, write_section

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Calculation helpers
# ---------------------------------------------------------------------------

def _sma(prices: list[float], period: int) -> float | None:
    """Simple moving average of the last `period` values."""
    if len(prices) < period:
        return None
    window = prices[-period:]
    return sum(window) / period


def _rsi(prices: list[float], period: int = 14) -> float | None:
    """
    Wilder's RSI using adj_close prices (most recent last).
    Returns a value 0–100, or None if insufficient data.
    """
    if len(prices) < period + 1:
        return None

    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(c, 0) for c in changes]
    losses = [abs(min(c, 0)) for c in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder smoothing for the rest
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _pct_change(current: float, reference: float | None) -> float | None:
    """(current / reference - 1) * 100. Returns None if reference is 0/None."""
    if not reference:
        return None
    return round((current / reference - 1) * 100, 2)


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def refresh_momentum(permno: int) -> None:
    """
    Recompute all momentum metrics for one company and upsert into `momentum`.
    Requires prices and ttm_cache to be populated first. Serialized via the
    shared writer lock: the price scheduler, queue runner, and CLI all call
    this for the same rows.
    """
    with write_section() as con:
        _refresh_momentum_locked(con, permno)


def _refresh_momentum_locked(con, permno: int) -> None:

    # NOT isnan: DuckDB evaluates NaN > 0 as TRUE, so NaN must be excluded
    # explicitly or one bad row poisons every SMA/RSI downstream.
    price_rows = con.execute("""
        SELECT price_date, adj_close
        FROM prices
        WHERE permno = ?
          AND adj_close IS NOT NULL
          AND NOT isnan(adj_close)
          AND adj_close > 0
        ORDER BY price_date ASC
    """, [permno]).fetchall()

    if not price_rows:
        return

    prices = [r[1] for r in price_rows]
    price_dates = [r[0] for r in price_rows]
    current = prices[-1]
    as_of = price_dates[-1]

    sma_50 = _sma(prices, settings.SMA_50D_WEEKS)
    sma_200 = _sma(prices, settings.SMA_200D_WEEKS)

    pct_from_200 = _pct_change(current, sma_200) if sma_200 else None

    # % from 52-week high (last 52 weekly bars)
    window_52w = prices[-52:] if len(prices) >= 52 else prices
    high_52w = max(window_52w)
    pct_from_52wh = _pct_change(current, high_52w)

    rsi_14 = _rsi(prices, settings.RSI_WEEKS)

    ttm_row = con.execute("""
        SELECT t.ttm_eps, t.ttm_ebitda, t.ttm_revenue,
               (SELECT cash FROM fundamentals
                WHERE permno = ? ORDER BY report_date DESC LIMIT 1) AS cash,
               (SELECT debt FROM fundamentals
                WHERE permno = ? ORDER BY report_date DESC LIMIT 1) AS debt
        FROM ttm_cache t
        WHERE t.permno = ?
    """, [permno, permno, permno]).fetchone()

    pe_ttm = None
    ev_ebitda = None
    ps_ttm = None

    if ttm_row:
        ttm_eps, ttm_ebitda, ttm_revenue, cash, debt = ttm_row

        if ttm_eps and ttm_eps != 0:
            pe_ttm = round(current / ttm_eps, 2)

        shares_row = con.execute("""
            SELECT shares_outstanding FROM fundamentals
            WHERE permno = ? AND shares_outstanding IS NOT NULL
            ORDER BY report_date DESC LIMIT 1
        """, [permno]).fetchone()

        if shares_row and shares_row[0]:
            shares = shares_row[0]  # millions
            market_cap = current * shares

            if ttm_revenue and ttm_revenue != 0:
                ps_ttm = round(market_cap / ttm_revenue, 2)

            # EV = market_cap + debt - cash (all in millions)
            ev = (market_cap + debt - cash
                  if debt is not None and cash is not None else None)
            if ev is not None and ttm_ebitda is not None and ttm_ebitda > 0:
                ev_ebitda = round(ev / ttm_ebitda, 2)

    con.execute("""
        INSERT OR REPLACE INTO momentum (
            permno, as_of_date, price_current,
            sma_50, sma_200,
            pct_from_52wh, pct_from_200, rsi_14,
            pe_ttm, ps_ttm, ev_ebitda_ttm,
            updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
    """, [
        permno, as_of, current,
        round(sma_50, 4) if sma_50 else None,
        round(sma_200, 4) if sma_200 else None,
        pct_from_52wh, pct_from_200, rsi_14,
        pe_ttm, ps_ttm, ev_ebitda,
    ])


def refresh_all_momentum() -> None:
    """Refresh momentum for every company with price data."""
    con = get_conn()
    permnos = [r[0] for r in con.execute(
        "SELECT DISTINCT permno FROM prices"
    ).fetchall()]

    for p in permnos:
        refresh_momentum(p)
    log.info("Momentum refreshed for %d companies.", len(permnos))


def get_company_momentum(permno: int) -> dict | None:
    """Fetch momentum metrics for a single company."""
    con = get_conn()
    row = con.execute("""
        SELECT price_current, sma_50, sma_200, pct_from_52wh, pct_from_200,
               rsi_14, pe_ttm, ps_ttm, ev_ebitda_ttm
        FROM momentum WHERE permno = ?
    """, [permno]).fetchone()
    if not row:
        return None
    cols = ["price_current","sma_50","sma_200","pct_from_52wh","pct_from_200",
            "rsi_14","pe_ttm","ps_ttm","ev_ebitda_ttm"]
    return dict(zip(cols, row))


if __name__ == "__main__":
    import sys

    import duckdb

    from alphamaxx.log import configure_logging
    configure_logging()
    try:
        refresh_all_momentum()
    except duckdb.IOException as e:
        sys.exit(f"Cannot open alphamaxx.db ({e}).\n"
                 "The app server is probably running — stop it first "
                 "(DuckDB allows one writer process).")
