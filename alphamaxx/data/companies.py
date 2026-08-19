"""Company identity, watchlist/portfolio flags, ingestion status, transcripts."""

from __future__ import annotations

import logging

from alphamaxx.data.db import get_conn, write_section

log = logging.getLogger(__name__)


def get_company_by_ticker(ticker: str) -> dict | None:
    """Return a company row by exact ticker."""
    con = get_conn()
    row = con.execute("""
        SELECT permno, ticker, name
        FROM companies
        WHERE UPPER(ticker) = UPPER(?)
        LIMIT 1
    """, [ticker]).fetchone()
    if not row:
        return None
    return {"permno": row[0], "ticker": row[1], "name": row[2]}


def search_companies(query: str, limit: int = 10) -> list[dict]:
    """Search for companies by ticker or name."""
    con = get_conn()
    rows = con.execute("""
        SELECT permno, ticker, name
        FROM companies
        WHERE UPPER(ticker) LIKE UPPER(?)
           OR UPPER(name)   LIKE UPPER(?)
        ORDER BY
            CASE WHEN UPPER(ticker) = UPPER(?) THEN 0
                 WHEN UPPER(ticker) LIKE UPPER(?) THEN 1
                 ELSE 2 END,
            ticker
        LIMIT ?
    """, [f"%{query}%", f"%{query}%", query, f"{query}%", limit]).fetchall()
    return [{"permno": r[0], "ticker": r[1], "name": r[2]} for r in rows]


def add_company(permno: int, ticker: str, name: str,
                sector: str = "", industry: str = "",
                exchange: str = "", watchlist: bool = True) -> None:
    with write_section() as con:
        con.execute("""
            INSERT OR REPLACE INTO companies (permno, ticker, name, sector, industry, exchange, in_watchlist)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [permno, ticker, name, sector, industry, exchange, watchlist])


def upsert_company_identity(
    permno: int,
    ticker: str,
    name: str,
    exchange: str = "",
    sector: str = "",
    industry: str = "",
    watchlist: bool = False,
) -> None:
    """Create or refresh a company identity without clobbering user flags."""
    with write_section() as con:
        existing = con.execute(
            "SELECT 1 FROM companies WHERE permno = ?", [permno]
        ).fetchone()
        if existing:
            con.execute("""
                UPDATE companies
                SET ticker = ?, name = ?, sector = COALESCE(NULLIF(?, ''), sector),
                    industry = COALESCE(NULLIF(?, ''), industry),
                    exchange = COALESCE(NULLIF(?, ''), exchange),
                    updated_at = CURRENT_TIMESTAMP
                WHERE permno = ?
            """, [ticker, name, sector, industry, exchange, permno])
        else:
            con.execute("""
                INSERT INTO companies (
                    permno, ticker, name, sector, industry, exchange,
                    in_watchlist, in_portfolio, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, FALSE, CURRENT_TIMESTAMP)
            """, [permno, ticker, name, sector, industry, exchange, watchlist])


def get_watchlist_summary() -> list[dict]:
    """
    Returns core metrics for all watchlist companies.
    Used by the main dashboard grid.
    """
    con = get_conn()
    rows = con.execute("""
        SELECT
            c.permno,
            c.ticker,
            c.name,
            t.ttm_revenue,
            t.ttm_ebitda,
            t.ttm_fcf,
            t.fcf_ex_sbc,
            t.gross_margin_pct,
            t.ebitda_margin_pct,
            t.fcf_margin_pct,
            m.price_current,
            m.pe_ttm,
            m.ev_ebitda_ttm,
            CASE
                WHEN m.sma_50 IS NOT NULL AND NOT isnan(m.sma_50) AND m.sma_50 != 0
                     AND m.price_current IS NOT NULL AND NOT isnan(m.price_current)
                THEN (m.price_current / m.sma_50 - 1) * 100
            END AS pct_from_50,
            m.pct_from_200,
            m.pct_from_52wh,
            m.rsi_14
        FROM companies c
        LEFT JOIN ttm_cache t  ON c.permno = t.permno
        LEFT JOIN momentum  m  ON c.permno = m.permno
        WHERE c.in_watchlist = TRUE
        ORDER BY c.ticker
    """).fetchall()

    cols = [
        "permno","ticker","name","ttm_revenue","ttm_ebitda","ttm_fcf",
        "fcf_ex_sbc","gross_margin_pct","ebitda_margin_pct","fcf_margin_pct",
        "price_current","pe_ttm","ev_ebitda_ttm","pct_from_50","pct_from_200",
        "pct_from_52wh","rsi_14"
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_portfolio_summary() -> list[dict]:
    """Returns portfolio companies with price, % from 50/200 SMA, and RSI."""
    con = get_conn()
    rows = con.execute("""
        SELECT
            c.permno,
            c.ticker,
            c.name,
            m.price_current,
            CASE
                WHEN m.sma_50 IS NOT NULL AND NOT isnan(m.sma_50) AND m.sma_50 != 0
                     AND m.price_current IS NOT NULL AND NOT isnan(m.price_current)
                THEN (m.price_current / m.sma_50 - 1) * 100
            END AS pct_from_50,
            m.pct_from_200,
            m.rsi_14
        FROM companies c
        LEFT JOIN momentum m ON c.permno = m.permno
        WHERE c.in_portfolio = TRUE
        ORDER BY c.ticker
    """).fetchall()

    cols = ["permno", "ticker", "name", "price_current", "pct_from_50", "pct_from_200", "rsi_14"]
    return [dict(zip(cols, r)) for r in rows]


def set_watchlist(permno: int, member: bool) -> None:
    """Add/remove a company from the watchlist."""
    with write_section() as con:
        con.execute("UPDATE companies SET in_watchlist = ? WHERE permno = ?", [member, permno])


def set_portfolio(permno: int, member: bool) -> None:
    """Add/remove a company from the portfolio."""
    with write_section() as con:
        con.execute("UPDATE companies SET in_portfolio = ? WHERE permno = ?", [member, permno])


def get_company_header(permno: int) -> dict | None:
    """Name/exchange/last-ingested for the stock page's ticker tape."""
    con = get_conn()
    row = con.execute(
        "SELECT name, exchange, last_ingested_at FROM companies WHERE permno = ?",
        [permno],
    ).fetchone()
    if not row:
        return None
    return {"name": row[0], "exchange": row[1], "last_ingested_at": row[2]}


def tracked_companies() -> list[tuple[int, str]]:
    """(permno, ticker) for every watchlist or portfolio company."""
    con = get_conn()
    return con.execute(
        "SELECT permno, ticker FROM companies "
        "WHERE in_watchlist = TRUE OR in_portfolio = TRUE ORDER BY ticker"
    ).fetchall()


def watchlist_companies() -> list[tuple[int, str]]:
    """(permno, ticker) for every watchlist company."""
    con = get_conn()
    return con.execute(
        "SELECT permno, ticker FROM companies WHERE in_watchlist = TRUE ORDER BY ticker"
    ).fetchall()


def companies_with_transcripts(limit: int = 100) -> list[dict]:
    """Companies that have at least one transcript, with their transcript count."""
    con = get_conn()
    rows = con.execute("""
        SELECT c.permno, c.ticker, c.name, COUNT(t.earnings_date) AS n_transcripts
        FROM companies c
        JOIN transcripts t ON c.permno = t.permno
        GROUP BY c.permno, c.ticker, c.name
        ORDER BY c.ticker
        LIMIT ?
    """, [limit]).fetchall()
    return [{"permno": r[0], "ticker": r[1], "name": r[2], "n_transcripts": r[3]}
            for r in rows]


def get_company_profile(permno: int) -> dict | None:
    """Full identity row for the stock page's company-info section."""
    con = get_conn()
    row = con.execute("""
        SELECT ticker, name, sector, industry, exchange, website, employees, ceo
        FROM companies WHERE permno = ?
    """, [permno]).fetchone()
    if not row:
        return None
    cols = ["ticker", "name", "sector", "industry", "exchange", "website", "employees", "ceo"]
    return dict(zip(cols, row))


def update_company_profile(permno: int, website: str | None = None,
                           employees: int | None = None,
                           ceo: str | None = None,
                           sector: str | None = None,
                           industry: str | None = None) -> None:
    """Fill in yfinance-sourced profile fields without clobbering non-null values."""
    with write_section() as con:
        con.execute("""
            UPDATE companies
            SET website = COALESCE(?, website),
                employees = COALESCE(?, employees),
                ceo = COALESCE(?, ceo),
                sector = COALESCE(?, sector),
                industry = COALESCE(?, industry)
            WHERE permno = ?
        """, [website, employees, ceo, sector, industry, permno])


def get_transcripts(permno: int) -> list[dict]:
    """Return earnings transcripts for a company."""
    con = get_conn()
    rows = con.execute("""
        SELECT earnings_date, fiscal_qtr, raw_text, ai_summary, sentiment
        FROM transcripts
        WHERE permno = ?
        ORDER BY earnings_date DESC
        LIMIT 8
    """, [permno]).fetchall()
    cols = ["earnings_date","fiscal_qtr","raw_text","ai_summary","sentiment"]
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Ingestion Status Tracking
# ---------------------------------------------------------------------------

def refresh_ingestion_status(permno: int) -> None:
    """Update ingestion tracking columns for one company."""
    con = get_conn()
    row = con.execute("SELECT name FROM companies WHERE permno = ?", [permno]).fetchone()
    name = row[0] if row else ""

    fund_count = con.execute(
        "SELECT COUNT(*) FROM fundamentals WHERE permno = ?", [permno]
    ).fetchone()[0]
    price_count = con.execute(
        "SELECT COUNT(*) FROM prices WHERE permno = ?", [permno]
    ).fetchone()[0]
    has_momentum = con.execute(
        "SELECT COUNT(*) FROM momentum WHERE permno = ?", [permno]
    ).fetchone()[0] > 0

    # ETFs, Trusts, and Funds have no corporate fundamentals
    is_etf = any(k in name for k in ["Trust", "Fund", "ETF", "Proshares", "Invesco", "Vanguard", "Ishares", "E T F"])

    if is_etf:
        if price_count > 0 and has_momentum:
            status = "complete"
        elif price_count > 0:
            status = "partial"
        else:
            status = "pending"
    else:
        if fund_count > 0 and price_count > 0 and has_momentum:
            status = "complete"
        elif fund_count > 0 or price_count > 0:
            status = "partial"
        else:
            status = "pending"

    with write_section() as wcon:
        wcon.execute("""
            UPDATE companies
            SET fundamentals_count = ?,
                prices_count = ?,
                ingestion_status = ?,
                last_ingested_at = CURRENT_TIMESTAMP
            WHERE permno = ?
        """, [fund_count, price_count, status, permno])


def backfill_ingestion_status() -> None:
    """Scan all companies and update ingestion status based on existing data."""
    con = get_conn()
    permnos = [r[0] for r in con.execute("SELECT permno FROM companies").fetchall()]
    for p in permnos:
        refresh_ingestion_status(p)
    log.info("Ingestion status backfilled for %d companies.", len(permnos))


def get_ingestion_summary() -> dict:
    """Return counts of companies by ingestion status."""
    con = get_conn()
    total = con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    complete = con.execute(
        "SELECT COUNT(*) FROM companies WHERE ingestion_status = 'complete'"
    ).fetchone()[0]
    partial = con.execute(
        "SELECT COUNT(*) FROM companies WHERE ingestion_status = 'partial'"
    ).fetchone()[0]
    pending = total - complete - partial
    return {"total": total, "complete": complete, "partial": partial, "pending": pending}


def get_companies_with_data(limit: int = 10) -> list[dict]:
    """Return companies that have complete ingestion data."""
    con = get_conn()
    rows = con.execute("""
        SELECT c.permno, c.ticker, c.name, m.price_current, m.pct_from_200
        FROM companies c
        LEFT JOIN momentum m ON c.permno = m.permno
        WHERE c.ingestion_status = 'complete'
        ORDER BY c.ticker
        LIMIT ?
    """, [limit]).fetchall()
    return [{"permno": r[0], "ticker": r[1], "name": r[2],
             "price": r[3], "pct_from_200": r[4]} for r in rows]
