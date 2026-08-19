"""TTM window SQL against a temp DuckDB: rolling sums, quarter_count gating,
idempotency, annual aggregation, dividends bucketing."""

import pytest

from alphamaxx.data import (
    get_annual_series,
    get_company_ttm,
    get_dividend_metrics,
    get_dividend_series,
    get_ttm_chart_series,
    refresh_ttm,
    upsert_dividends,
)
from alphamaxx.data.db import get_conn


def _ttm_rows(permno):
    return get_conn().execute("""
        SELECT fiscal_qtr, ttm_revenue, ttm_eps, quarter_count
        FROM fundamentals_ttm WHERE permno = ? ORDER BY fiscal_qtr
    """, [permno]).fetchall()


def test_rolling_ttm_sums(synthetic_company):
    refresh_ttm(synthetic_company)
    rows = _ttm_rows(synthetic_company)
    assert len(rows) == 8
    # Quarter 4 (2023Q4): revenue 100+200+300+400 = 1000, 4 quarters
    q4 = rows[3]
    assert q4[0] == "2023Q4"
    assert q4[1] == pytest.approx(1000.0)
    assert q4[2] == pytest.approx(4.0)  # 4 × eps 1.0
    assert q4[3] == 4
    # Quarter 8 (2024Q4): 500+600+700+800 = 2600
    assert rows[7][1] == pytest.approx(2600.0)
    # First quarter only has a 1-quarter window
    assert rows[0][3] == 1


def test_quarter_count_gates_chart_series(synthetic_company):
    refresh_ttm(synthetic_company)
    series = get_ttm_chart_series(synthetic_company, "revenue", "detail")
    # Only the 5 windows with 4 full quarters qualify
    assert len(series) == 5
    assert series[0]["fiscal_qtr"] == "2023Q4"
    assert series[-1]["value"] == pytest.approx(2600.0)
    # Margin column rides along for the overlay line
    assert series[-1]["margin"] == pytest.approx(60.0)  # gross 60%


def test_refresh_is_idempotent(synthetic_company):
    refresh_ttm(synthetic_company)
    first = _ttm_rows(synthetic_company)
    refresh_ttm(synthetic_company)
    second = _ttm_rows(synthetic_company)
    assert first == second


def test_ttm_cache_takes_latest_full_window(synthetic_company):
    refresh_ttm(synthetic_company)
    ttm = get_company_ttm(synthetic_company)
    assert ttm["ttm_revenue"] == pytest.approx(2600.0)
    assert ttm["fcf_ex_sbc"] == pytest.approx(ttm["ttm_fcf"] - ttm["ttm_sbc"])


def test_ttm_refresh_clears_stale_cache_when_snapshot_has_under_four_quarters(
    synthetic_company,
):
    con = get_conn()
    refresh_ttm(synthetic_company)
    assert get_company_ttm(synthetic_company) is not None

    con.execute(
        "DELETE FROM fundamentals WHERE permno = ? AND fiscal_qtr < '2024Q2'",
        [synthetic_company],
    )
    refresh_ttm(synthetic_company)

    assert con.execute(
        "SELECT COUNT(*) FROM ttm_cache WHERE permno = ?", [synthetic_company]
    ).fetchone()[0] == 0


def test_annual_series_full_years_only(synthetic_company):
    annual = get_annual_series(synthetic_company, "revenue")
    # 2023 (100+200+300+400) and 2024 (500+600+700+800); no partial years
    assert [r["fiscal_qtr"] for r in annual] == ["2023", "2024"]
    assert annual[0]["value"] == pytest.approx(1000.0)
    assert annual[1]["value"] == pytest.approx(2600.0)
    assert annual[1]["yoy_pct"] == pytest.approx(160.0)
    # Stock metric takes the final quarter's value, not a sum
    shares = get_annual_series(synthetic_company, "shares")
    assert shares[1]["value"] == pytest.approx(900.0)


def test_dividend_quarter_buckets(synthetic_company):
    upsert_dividends(synthetic_company, [
        ("2024-02-10", 0.25), ("2024-05-10", 0.25),
        ("2024-05-20", 0.05),  # same quarter as previous — should sum
        (None, 0.10),           # bad rows skipped
        ("2024-08-10", None),
        ("2024-08-11", -1.0),
    ])
    series = get_dividend_series(synthetic_company)
    assert [r["fiscal_qtr"] for r in series] == ["2024Q1", "2024Q2"]
    assert series[1]["value"] == pytest.approx(0.30)


def test_dividend_metrics_ttm_yield_payout(synthetic_company):
    from datetime import date, timedelta

    refresh_ttm(synthetic_company)  # ttm_eps = 4.0 (four quarters of 1.0)
    get_conn().execute(
        "INSERT OR REPLACE INTO momentum (permno, price_current) VALUES (?, 100.0)",
        [synthetic_company],
    )
    today = date.today()
    upsert_dividends(synthetic_company, [
        (today - timedelta(days=d), 0.25) for d in (30, 120, 210, 300)
    ] + [
        (today - timedelta(days=400), 5.0),  # outside the trailing year
    ])

    m = get_dividend_metrics(synthetic_company)
    assert m["ttm_dps"] == pytest.approx(1.0)
    assert m["div_yield_pct"] == pytest.approx(1.0)     # 1.0 / 100 * 100
    assert m["payout_pct"] == pytest.approx(25.0)       # 1.0 / 4.0 * 100


def test_dividend_metrics_none_for_nonpayer(synthetic_company):
    assert get_dividend_metrics(synthetic_company) is None
