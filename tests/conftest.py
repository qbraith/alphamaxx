"""Test fixtures. ALPHAMAXX_DB is pointed at a temp file BEFORE the package
is imported, so the production alphamaxx.db is never touched by tests."""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="alphamaxx-test-")
os.environ["ALPHAMAXX_DB"] = os.path.join(_TMP, "test.db")
os.environ["ALPHAMAXX_PARQUET_DIR"] = _TMP
os.environ["ALPHAMAXX_STATE_DIR"] = _TMP

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Keep the suite hermetic. TestClient startup and full-page renders would
    otherwise start the price-updater scheduler and spawn yfinance/Nasdaq
    threads (indices, PE/PEG, earnings) whose results the tests ignore.
    Route modules bind these names at import (`from x import y`), so the stubs
    are applied to both the source modules and the consuming modules."""
    import alphamaxx.services.earnings as earnings
    import alphamaxx.services.price_updater as price_updater
    import alphamaxx.services.yf_client as yf_client
    import alphamaxx.web.routes.lists as lists_routes
    import alphamaxx.web.routes.tools as tools_routes
    import alphamaxx.web.shell as shell

    indices = [{"name": "DOW", "pct": 0.0}, {"name": "S&P 500", "pct": 0.0},
               {"name": "NASDAQ", "pct": 0.0}]
    monkeypatch.setattr(price_updater, "start_daily_updater", lambda: None)
    monkeypatch.setattr(yf_client, "get_indices", lambda: indices)
    monkeypatch.setattr(shell, "get_indices", lambda: indices)
    monkeypatch.setattr(yf_client, "fetch_yf_valuations", lambda tickers: {})
    monkeypatch.setattr(lists_routes, "fetch_yf_valuations", lambda tickers: {})
    monkeypatch.setattr(earnings, "fetch_earnings_two_weeks", lambda: ([], False))
    monkeypatch.setattr(tools_routes, "fetch_earnings_two_weeks", lambda: ([], False))


@pytest.fixture(scope="session")
def db():
    """Initialized empty schema on the temp database."""
    from alphamaxx.data import get_conn, init_db
    init_db()
    return get_conn()


@pytest.fixture()
def synthetic_company(db):
    """One company with 8 synthetic quarters of fundamentals.

    Values are chosen so rolling sums are easy to assert:
    revenue = 100, 200, …, 800 by quarter; eps = 1.0 per quarter.
    """
    permno = 999001
    db.execute("DELETE FROM fundamentals WHERE permno = ?", [permno])
    db.execute("DELETE FROM fundamentals_ttm WHERE permno = ?", [permno])
    db.execute("DELETE FROM ttm_cache WHERE permno = ?", [permno])
    db.execute("DELETE FROM momentum WHERE permno = ?", [permno])
    db.execute("DELETE FROM dividends WHERE permno = ?", [permno])
    db.execute("DELETE FROM valuation_cache WHERE ticker = 'TEST'")
    db.execute(
        "INSERT OR REPLACE INTO companies (permno, ticker, name) VALUES (?, 'TEST', 'Test Corp')",
        [permno],
    )
    db.execute("UPDATE companies SET sector=NULL, industry=NULL WHERE permno=?", [permno])
    quarters = [
        ("2023Q1", "2023-04-15"), ("2023Q2", "2023-07-15"),
        ("2023Q3", "2023-10-15"), ("2023Q4", "2024-01-15"),
        ("2024Q1", "2024-04-15"), ("2024Q2", "2024-07-15"),
        ("2024Q3", "2024-10-15"), ("2024Q4", "2025-01-15"),
    ]
    for i, (fq, rd) in enumerate(quarters, start=1):
        db.execute("""
            INSERT OR REPLACE INTO fundamentals (
                permno, fiscal_qtr, report_date, revenue, cogs, gross_profit,
                ebitda, ebit, net_income, eps_diluted, fcf, sbc, capex,
                pretax_income, income_tax, shareholders_equity,
                shares_outstanding, shares_diluted, cash, debt
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [
            permno, fq, rd,
            i * 100.0,         # revenue
            i * 40.0,          # cogs
            i * 60.0,          # gross_profit
            i * 30.0,          # ebitda
            i * 25.0,          # ebit
            i * 20.0,          # net_income
            1.0,               # eps_diluted
            i * 15.0,          # fcf
            i * 2.0,           # sbc
            i * 5.0,           # capex
            i * 25.0,          # pretax income
            i * 5.0,           # income tax (20%)
            1000.0 + i * 10,   # shareholders' equity
            900.0,             # period-end shares outstanding
            1000.0,            # shares_diluted
            500.0 + i,         # cash
            200.0,             # debt
        ])
    return permno
