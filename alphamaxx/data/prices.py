"""Price-history queries: weekly series, P/E cloud, and correlations."""

from __future__ import annotations

import math
from datetime import timedelta

from alphamaxx.config import settings
from alphamaxx.data.db import get_conn, write_section


def get_price_history(permno: int, weeks: int = 260) -> list[dict]:
    """Return weekly price history (default 5 years), newest first."""
    con = get_conn()
    rows = con.execute("""
        SELECT price_date, adj_close, volume
        FROM prices
        WHERE permno = ? AND adj_close IS NOT NULL AND NOT isnan(adj_close)
        ORDER BY price_date DESC
        LIMIT ?
    """, [permno, weeks]).fetchall()
    return [{"date": str(r[0]), "price": r[1], "volume": r[2]} for r in rows]


def upsert_price_bar(permno: int, price_date, open_, high, low, close,
                     adj_close, volume: int,
                     split_factor: float | None = None) -> None:
    """Upsert one weekly OHLC bar without erasing an existing split marker."""
    with write_section() as con:
        con.execute("""
            INSERT INTO prices
                (permno, price_date, open, high, low, close, adj_close, volume, split_factor)
            VALUES (?,?,?,?,?,?,?,?,COALESCE(?, 1.0))
            ON CONFLICT (permno, price_date) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                adj_close = excluded.adj_close,
                volume = excluded.volume,
                split_factor = CASE
                    WHEN ? IS NULL THEN prices.split_factor
                    ELSE excluded.split_factor
                END
        """, [
            permno, price_date, open_, high, low, close, adj_close, volume,
            split_factor, split_factor,
        ])


def upsert_stock_splits(permno: int, events: list[tuple[object, float]]) -> int:
    """Persist authoritative split dates and remove legacy weekly duplicates."""
    valid = []
    for event_date, raw_factor in events:
        try:
            factor = float(raw_factor)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(factor) or factor <= 0 or factor == 1.0:
            continue
        valid.append((permno, event_date, factor))
    if not valid:
        return 0
    with write_section() as con:
        for event_permno, raw_date, factor in valid:
            event_date = raw_date.date() if hasattr(raw_date, "date") else raw_date
            week_start = event_date - timedelta(days=event_date.weekday())
            week_end = week_start + timedelta(days=6)
            # Older AlphaMaxx versions attached a split to the weekly bar's
            # Monday. Keeping both dates would apply one split twice to EPS.
            con.execute("""
                DELETE FROM stock_splits
                WHERE permno = ? AND split_date BETWEEN ? AND ?
                  AND split_date != ? AND ABS(split_factor - ?) < 1e-12
            """, [event_permno, week_start, week_end, event_date, factor])
            con.execute("""
                UPDATE prices SET split_factor = 1.0
                WHERE permno = ? AND price_date BETWEEN ? AND ?
                  AND ABS(split_factor - ?) < 1e-12
            """, [event_permno, week_start, week_end, factor])
            con.execute("""
                INSERT INTO stock_splits (permno, split_date, split_factor)
                VALUES (?, ?, ?)
                ON CONFLICT (permno, split_date) DO UPDATE SET
                    split_factor = excluded.split_factor
            """, [event_permno, event_date, factor])
    return len(valid)


def get_latest_price_date() -> str | None:
    """Most recent price_date across all companies (for the status bar)."""
    con = get_conn()
    row = con.execute("SELECT MAX(price_date) FROM prices").fetchone()
    return str(row[0]) if row and row[0] else None


def get_pe_history(permno: int, weeks: int | None = None) -> dict | None:
    """
    Weekly trailing P/E history with ±1σ / ±2σ bands (default ~10 years).

    Each weekly adj_close is divided by the most recent TTM EPS as of that week
    (forward-filled via an ASOF JOIN). Returns the series plus the mean and
    standard-deviation bands for plotting a "P/E cloud".
    """
    weeks = weeks or settings.PE_WEEKS
    con = get_conn()
    rows = con.execute("""
        SELECT p.price_date, p.adj_close / f.ttm_eps AS pe
        FROM prices p
        ASOF JOIN fundamentals_ttm f
          ON p.permno = f.permno AND p.price_date >= f.report_date
        WHERE p.permno = ?
          AND f.ttm_eps > 0
          AND p.adj_close > 0
          AND NOT isnan(p.adj_close)
        ORDER BY p.price_date
    """, [permno]).fetchall()
    if not rows:
        return None
    rows = rows[-weeks:]
    # Drop absurd outliers (e.g. near-zero EPS spikes) before computing stats.
    pes = [r[1] for r in rows if r[1] is not None and 0 < r[1] < 500]
    if len(pes) < 8:
        return None
    mean = sum(pes) / len(pes)
    var = sum((x - mean) ** 2 for x in pes) / len(pes)
    std = var ** 0.5
    # "Current" is the latest week's PE when sane; the last inlier otherwise.
    latest = rows[-1][1]
    current = latest if (latest is not None and 0 < latest < 500) else pes[-1]
    return {
        "dates": [str(r[0]) for r in rows],
        "pe": [round(r[1], 2) if r[1] is not None else None for r in rows],
        "mean": round(mean, 2),
        "std": round(std, 2),
        "current": round(current, 2),
    }


def get_correlation_matrix(permnos: list[int], tickers: list[str]) -> dict | None:
    """
    Compute pairwise return correlation matrix from weekly adj_close prices.
    Returns {"tickers": [...], "matrix": [[float|None, ...], ...]} or None if
    insufficient data. Cells are None where the correlation is undefined
    (e.g. a constant price series).
    """
    import numpy as np
    con = get_conn()

    series = {}
    for permno, ticker in zip(permnos, tickers):
        rows = con.execute("""
            SELECT price_date, adj_close FROM prices
            WHERE permno = ? AND adj_close IS NOT NULL
              AND NOT isnan(adj_close) AND adj_close > 0
            ORDER BY price_date DESC LIMIT ?
        """, [permno, settings.CORRELATION_WEEKS]).fetchall()
        if len(rows) >= 10:
            series[ticker] = {r[0]: r[1] for r in rows}

    if len(series) < 2:
        return None

    all_dates = set.intersection(*[set(s.keys()) for s in series.values()])
    if len(all_dates) < 10:
        return None

    sorted_dates = sorted(all_dates)
    tickers_with_data = list(series.keys())

    price_matrix = np.array([
        [series[t][d] for d in sorted_dates] for t in tickers_with_data
    ])
    returns = np.diff(np.log(price_matrix), axis=1)

    corr = np.corrcoef(returns)
    corr_list = [
        [
            None if math.isnan(corr[i][j]) else round(float(corr[i][j]), 2)
            for j in range(len(tickers_with_data))
        ]
        for i in range(len(tickers_with_data))
    ]

    return {"tickers": tickers_with_data, "matrix": corr_list}
