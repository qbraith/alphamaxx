"""Company identity/status logic on a temp DB: ETF classifier, flag
preservation across re-ingestion, and search ranking."""

from alphamaxx.data import (
    refresh_ingestion_status,
    search_companies,
    set_watchlist,
    upsert_company_identity,
)


def _status(db, permno):
    return db.execute(
        "SELECT ingestion_status FROM companies WHERE permno = ?", [permno]
    ).fetchone()[0]


def _seed(db, permno, ticker, name, prices=0, fundamentals=0, momentum=False):
    db.execute("DELETE FROM momentum WHERE permno = ?", [permno])
    db.execute("DELETE FROM prices WHERE permno = ?", [permno])
    db.execute("DELETE FROM fundamentals WHERE permno = ?", [permno])
    db.execute(
        "INSERT OR REPLACE INTO companies (permno, ticker, name) VALUES (?,?,?)",
        [permno, ticker, name],
    )
    for i in range(prices):
        db.execute(
            "INSERT OR REPLACE INTO prices (permno, price_date, adj_close) VALUES (?,?,100.0)",
            [permno, f"2024-01-{i + 1:02d}"],
        )
    qtr_ends = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"]
    for i in range(fundamentals):
        db.execute("""
            INSERT OR REPLACE INTO fundamentals (permno, fiscal_qtr, report_date, revenue)
            VALUES (?,?,?,100.0)
        """, [permno, f"2024Q{i + 1}", qtr_ends[i]])
    if momentum:
        db.execute(
            "INSERT OR REPLACE INTO momentum (permno, price_current) VALUES (?, 100.0)",
            [permno],
        )


def test_etf_complete_without_fundamentals(db):
    _seed(db, 777001, "VTST", "Vanguard Test ETF", prices=3, momentum=True)
    refresh_ingestion_status(777001)
    assert _status(db, 777001) == "complete"


def test_operating_company_needs_fundamentals(db):
    # Same data shape as the ETF above, but a corporate name: only partial.
    _seed(db, 777002, "ACME", "Acme Corp", prices=3, momentum=True)
    refresh_ingestion_status(777002)
    assert _status(db, 777002) == "partial"

    _seed(db, 777003, "ACMF", "Acme Full Corp", prices=3, fundamentals=2, momentum=True)
    refresh_ingestion_status(777003)
    assert _status(db, 777003) == "complete"


def test_no_data_is_pending(db):
    _seed(db, 777004, "NODA", "Nodata Corp")
    refresh_ingestion_status(777004)
    assert _status(db, 777004) == "pending"


def test_upsert_identity_preserves_user_flags(db):
    _seed(db, 777005, "FLAG", "Flag Corp")
    set_watchlist(777005, True)

    upsert_company_identity(777005, "FLAG", "Flag Corporation Renamed",
                            exchange="NASDAQ")
    row = db.execute(
        "SELECT name, exchange, in_watchlist FROM companies WHERE permno = 777005"
    ).fetchone()
    assert row[0] == "Flag Corporation Renamed"
    assert row[1] == "NASDAQ"
    assert row[2] is True  # user flag survives re-ingestion


def test_search_ranks_exact_ticker_first(db):
    _seed(db, 777006, "SRCH", "Search Industries")
    _seed(db, 777007, "SR", "SRCH-adjacent Name Corp")  # matches by name only
    results = search_companies("SRCH")
    tickers = [r["ticker"] for r in results]
    assert tickers[0] == "SRCH"
    assert "SR" in tickers
