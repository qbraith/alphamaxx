"""Durable cache of yfinance valuation ratios (PE / PEG / forward PE)."""

from __future__ import annotations

from alphamaxx.data.db import get_conn, write_section


def get_valuation_cache(tickers: list[str]) -> dict[str, dict]:
    """Return {ticker: {'pe', 'peg', 'forward_pe', 'age_s'}} for cached
    yfinance valuation ratios. age_s lets callers serve stale rows while
    refreshing them."""
    if not tickers:
        return {}
    con = get_conn()
    placeholders = ",".join("?" * len(tickers))
    rows = con.execute(
        f"""SELECT ticker, pe, peg, forward_pe,
                   date_diff('second', updated_at, CAST(now() AS TIMESTAMP))
            FROM valuation_cache WHERE ticker IN ({placeholders})""",
        list(tickers),
    ).fetchall()
    return {r[0]: {"pe": r[1], "peg": r[2], "forward_pe": r[3], "age_s": r[4]} for r in rows}


def upsert_valuation_cache(ticker: str, pe: float | None, peg: float | None,
                            forward_pe: float | None) -> None:
    """Store/refresh a ticker's PE/PEG/forward-PE in the durable valuation
    cache. Serialized: the price scheduler and yf-valuations thread share
    rows."""
    with write_section() as con:
        con.execute(
            "INSERT OR REPLACE INTO valuation_cache (ticker, pe, peg, forward_pe, updated_at) "
            "VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
            [ticker, pe, peg, forward_pe],
        )
