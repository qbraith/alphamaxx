"""Fundamentals queries: quarterly series, TTM pre-aggregation, segments,
balance-sheet snapshots. All dashboard reads must stay < 50ms."""

from __future__ import annotations

import logging

from alphamaxx.config import settings
from alphamaxx.data.db import get_conn, serialized_write, write_section

log = logging.getLogger(__name__)

# Increment whenever a persisted TTM formula or source-field semantic changes.
TTM_CALCULATION_VERSION = 2

TTM_CHART_COLUMNS = {
    "revenue": "ttm_revenue",
    "ebitda": "ttm_ebitda",
    "net_income": "ttm_net_income",
    "fcf": "ttm_fcf",
    "eps": "ttm_eps",
    "sbc": "ttm_sbc",
    "capex": "ttm_capex",
}

# Margin-% overlay column per metric (detail charts show it on a second axis)
TTM_MARGIN_COLUMNS = {
    "revenue": "gross_margin_pct",
    "ebitda": "ebitda_margin_pct",
    "net_income": "net_margin_pct",
    "fcf": "fcf_margin_pct",
}


# ---------------------------------------------------------------------------
# TTM Recalculation
# ---------------------------------------------------------------------------

def refresh_ttm(permno: int) -> None:
    """
    Recompute TTM aggregates for one company from the last 4 quarters.
    Stores result in ttm_cache for sub-millisecond dashboard reads.
    """
    refresh_ttm_history(permno)
    _refresh_ttm_cache_from_history(permno)


def _refresh_ttm_cache_from_history(permno: int) -> None:
    """Update the latest TTM cache row from precomputed historical TTM rows."""
    with write_section() as con:
        # A full fundamentals refresh may legitimately shrink below four
        # usable quarters. Clear the prior snapshot first so an older issuer's
        # TTM row cannot survive when the SELECT below produces no row.
        con.execute("DELETE FROM ttm_cache WHERE permno = ?", [permno])
        con.execute("""
        INSERT OR REPLACE INTO ttm_cache (
            permno, as_of_date, ttm_revenue, ttm_gross_profit, ttm_ebitda,
            ttm_ebit, ttm_net_income, ttm_eps, ttm_fcf, ttm_sbc, ttm_capex,
            gross_margin_pct, ebitda_margin_pct, fcf_margin_pct, fcf_ex_sbc,
            ttm_pretax_income, ttm_income_tax, avg_diluted_shares,
            shares_outstanding, invested_capital, avg_invested_capital,
            normalized_tax_rate, roic_pct, roic_3y_median_pct,
            fcf_conversion_pct, fcf_conversion_3y_median_pct, fcf_per_share,
            net_debt_ebitda, gross_margin_yoy_bps, ebitda_margin_yoy_bps,
            fcf_per_share_yoy_pct, fcf_per_share_3y_cagr_pct,
            shares_outstanding_yoy_pct, shares_outstanding_3y_cagr_pct,
            qtr_revenue_yoy_pct, qtr_eps_yoy_pct, calculation_version,
            updated_at
        )
        SELECT
            permno,
            report_date AS as_of_date,
            ttm_revenue,
            ttm_gross_profit,
            ttm_ebitda,
            ttm_ebit,
            ttm_net_income,
            ttm_eps,
            ttm_fcf,
            ttm_sbc,
            ttm_capex,
            gross_margin_pct,
            ebitda_margin_pct,
            fcf_margin_pct,
            fcf_ex_sbc,
            ttm_pretax_income,
            ttm_income_tax,
            avg_diluted_shares,
            shares_outstanding,
            invested_capital,
            avg_invested_capital,
            normalized_tax_rate,
            roic_pct,
            roic_3y_median_pct,
            fcf_conversion_pct,
            fcf_conversion_3y_median_pct,
            fcf_per_share,
            net_debt_ebitda,
            gross_margin_yoy_bps,
            ebitda_margin_yoy_bps,
            fcf_per_share_yoy_pct,
            fcf_per_share_3y_cagr_pct,
            shares_outstanding_yoy_pct,
            shares_outstanding_3y_cagr_pct,
            qtr_revenue_yoy_pct,
            qtr_eps_yoy_pct,
            calculation_version,
            CURRENT_TIMESTAMP
        FROM fundamentals_ttm
        WHERE permno = ? AND quarter_count = ?
        ORDER BY fiscal_qtr DESC
        LIMIT 1
    """, [permno, settings.TTM_WINDOW])


def refresh_ttm_history(permno: int) -> None:
    """
    Recompute quarter-by-quarter TTM values for one company.
    Each TTM value is the current quarter plus the three preceding quarters.
    Serialized: the DELETE+INSERT pair must not interleave with other writers.
    """
    with write_section() as con:
        con.execute("BEGIN TRANSACTION")
        try:
            _refresh_ttm_history_unlocked(permno)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise


def _refresh_ttm_history_unlocked(permno: int) -> None:
    con = get_conn()
    con.execute("DELETE FROM fundamentals_ttm WHERE permno = ?", [permno])
    con.execute("""
        INSERT INTO fundamentals_ttm (
            permno, fiscal_qtr, report_date,
            ttm_revenue, ttm_gross_profit, ttm_ebitda, ttm_ebit,
            ttm_net_income, ttm_eps, ttm_fcf, ttm_sbc, ttm_capex,
            fcf_ex_sbc, gross_margin_pct, ebitda_margin_pct, net_margin_pct,
            fcf_margin_pct, shares_diluted, shares_outstanding, cash, debt,
            ttm_pretax_income, ttm_income_tax, avg_diluted_shares,
            invested_capital, avg_invested_capital, normalized_tax_rate,
            roic_pct, roic_3y_median_pct, fcf_conversion_pct,
            fcf_conversion_3y_median_pct, fcf_per_share, net_debt_ebitda,
            gross_margin_yoy_bps, ebitda_margin_yoy_bps,
            fcf_per_share_yoy_pct, fcf_per_share_3y_cagr_pct,
            shares_outstanding_yoy_pct, shares_outstanding_3y_cagr_pct,
            qtr_revenue_yoy_pct, qtr_eps_yoy_pct, quarter_count,
            calculation_version, updated_at
        )
        WITH latest_basis AS (
            SELECT MAX(report_date) AS basis_date
            FROM fundamentals
            WHERE permno = ?
        ),
        adjusted AS (
            SELECT
                f.*,
                CAST(SUBSTR(f.fiscal_qtr, 1, 4) AS INTEGER) * 4
                    + CAST(RIGHT(f.fiscal_qtr, 1) AS INTEGER) - 1 AS qtr_ord,
                COALESCE((
                    SELECT EXP(SUM(LN(s.split_factor)))
                    FROM stock_splits s, latest_basis lb
                    WHERE s.permno = f.permno
                      AND s.split_date > f.report_date
                      AND s.split_date <= lb.basis_date
                      AND s.split_factor > 0
                ), 1.0) AS split_multiplier
            FROM fundamentals f
            WHERE f.permno = ?
        ),
        normalized AS (
            SELECT
                a.*,
                eps_diluted / NULLIF(split_multiplier, 0) AS eps_diluted_adj,
                shares_diluted * split_multiplier AS shares_diluted_adj,
                shares_outstanding * split_multiplier AS shares_outstanding_adj,
                CASE WHEN debt IS NOT NULL AND shareholders_equity IS NOT NULL
                          AND cash IS NOT NULL
                     THEN debt + shareholders_equity - cash END AS invested_capital
            FROM adjusted a
        ),
        windowed_raw AS (
            SELECT n.*,
                COUNT(*) OVER w AS quarter_count,
                MAX(qtr_ord) OVER w - MIN(qtr_ord) OVER w AS quarter_span,
                SUM(revenue) OVER w AS revenue_sum,
                COUNT(revenue) OVER w AS revenue_count,
                SUM(gross_profit) OVER w AS gross_profit_sum,
                COUNT(gross_profit) OVER w AS gross_profit_count,
                SUM(ebitda) OVER w AS ebitda_sum,
                COUNT(ebitda) OVER w AS ebitda_count,
                SUM(ebit) OVER w AS ebit_sum,
                COUNT(ebit) OVER w AS ebit_count,
                SUM(net_income) OVER w AS net_income_sum,
                COUNT(net_income) OVER w AS net_income_count,
                SUM(eps_diluted_adj) OVER w AS eps_sum,
                COUNT(eps_diluted_adj) OVER w AS eps_count,
                SUM(fcf) OVER w AS fcf_sum,
                COUNT(fcf) OVER w AS fcf_count,
                SUM(sbc) OVER w AS sbc_sum,
                COUNT(sbc) OVER w AS sbc_count,
                SUM(capex) OVER w AS capex_sum,
                COUNT(capex) OVER w AS capex_count,
                SUM(pretax_income) OVER w AS pretax_sum,
                COUNT(pretax_income) OVER w AS pretax_count,
                SUM(income_tax) OVER w AS tax_sum,
                COUNT(income_tax) OVER w AS tax_count,
                AVG(shares_diluted_adj) OVER w AS diluted_avg,
                COUNT(shares_diluted_adj) OVER w AS diluted_count
            FROM normalized n
            WINDOW w AS (
                PARTITION BY permno
                ORDER BY qtr_ord
                ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
            )
        ),
        rolling AS (
            SELECT wraw.*,
                CASE WHEN quarter_count = 4 AND quarter_span = 3 AND revenue_count = 4
                     THEN revenue_sum END AS ttm_revenue,
                CASE WHEN quarter_count = 4 AND quarter_span = 3 AND gross_profit_count = 4
                     THEN gross_profit_sum END AS ttm_gross_profit,
                CASE WHEN quarter_count = 4 AND quarter_span = 3 AND ebitda_count = 4
                     THEN ebitda_sum END AS ttm_ebitda,
                CASE WHEN quarter_count = 4 AND quarter_span = 3 AND ebit_count = 4
                     THEN ebit_sum END AS ttm_ebit,
                CASE WHEN quarter_count = 4 AND quarter_span = 3 AND net_income_count = 4
                     THEN net_income_sum END AS ttm_net_income,
                CASE WHEN quarter_count = 4 AND quarter_span = 3 AND eps_count = 4
                     THEN eps_sum END AS ttm_eps,
                CASE WHEN quarter_count = 4 AND quarter_span = 3 AND fcf_count = 4
                     THEN fcf_sum END AS ttm_fcf,
                CASE WHEN quarter_count = 4 AND quarter_span = 3 AND sbc_count = 4
                     THEN sbc_sum END AS ttm_sbc,
                CASE WHEN quarter_count = 4 AND quarter_span = 3 AND capex_count = 4
                     THEN capex_sum END AS ttm_capex,
                CASE WHEN quarter_count = 4 AND quarter_span = 3 AND pretax_count = 4
                     THEN pretax_sum END AS ttm_pretax_income,
                CASE WHEN quarter_count = 4 AND quarter_span = 3 AND tax_count = 4
                     THEN tax_sum END AS ttm_income_tax,
                CASE WHEN quarter_count = 4 AND quarter_span = 3 AND diluted_count = 4
                     THEN diluted_avg END AS avg_diluted_shares
            FROM windowed_raw wraw
        ),
        margins AS (
            SELECT r.*,
                ttm_gross_profit / NULLIF(ttm_revenue, 0) * 100 AS gross_margin_pct,
                ttm_ebitda / NULLIF(ttm_revenue, 0) * 100 AS ebitda_margin_pct,
                ttm_net_income / NULLIF(ttm_revenue, 0) * 100 AS net_margin_pct,
                ttm_fcf / NULLIF(ttm_revenue, 0) * 100 AS fcf_margin_pct,
                (SELECT MEDIAN(h.ttm_income_tax / h.ttm_pretax_income)
                 FROM rolling h
                 WHERE h.qtr_ord BETWEEN r.qtr_ord - 11 AND r.qtr_ord
                   AND h.ttm_pretax_income > 0
                   AND h.ttm_income_tax / h.ttm_pretax_income BETWEEN 0 AND 0.5
                ) AS normalized_tax_rate
            FROM rolling r
        ),
        capital AS (
            SELECT m.*,
                (m.invested_capital + prior.invested_capital) / 2.0 AS avg_invested_capital,
                prior.gross_margin_pct AS prior_gross_margin_pct,
                prior.ebitda_margin_pct AS prior_ebitda_margin_pct,
                prior.revenue AS prior_qtr_revenue,
                prior.eps_diluted_adj AS prior_qtr_eps
            FROM margins m
            LEFT JOIN margins prior
              ON prior.permno = m.permno AND prior.qtr_ord = m.qtr_ord - 4
        ),
        core AS (
            SELECT cap.*,
                CASE WHEN avg_invested_capital > 0 AND normalized_tax_rate IS NOT NULL
                     THEN ttm_ebit * (1 - normalized_tax_rate)
                          / avg_invested_capital * 100 END AS roic_pct,
                CASE WHEN ttm_net_income > 0
                     THEN ttm_fcf / ttm_net_income * 100 END AS fcf_conversion_pct,
                CASE WHEN avg_diluted_shares > 0
                     THEN ttm_fcf / avg_diluted_shares END AS fcf_per_share,
                CASE WHEN ttm_ebitda > 0 AND debt IS NOT NULL AND cash IS NOT NULL
                     THEN (debt - cash) / ttm_ebitda END AS net_debt_ebitda
            FROM capital cap
        ),
        enriched AS (
            SELECT c.*,
                (SELECT MEDIAN(h.roic_pct) FROM core h
                 WHERE h.qtr_ord BETWEEN c.qtr_ord - 11 AND c.qtr_ord
                   AND h.roic_pct IS NOT NULL) AS roic_3y_median_pct,
                (SELECT MEDIAN(h.fcf_conversion_pct) FROM core h
                 WHERE h.qtr_ord BETWEEN c.qtr_ord - 11 AND c.qtr_ord
                   AND h.fcf_conversion_pct IS NOT NULL) AS fcf_conversion_3y_median_pct,
                prior.fcf_per_share AS prior_fcf_per_share,
                prior.shares_outstanding_adj AS prior_shares_outstanding,
                prior3.fcf_per_share AS prior3_fcf_per_share,
                prior3.shares_outstanding_adj AS prior3_shares_outstanding
            FROM core c
            LEFT JOIN core prior
              ON prior.permno = c.permno AND prior.qtr_ord = c.qtr_ord - 4
            LEFT JOIN core prior3
              ON prior3.permno = c.permno AND prior3.qtr_ord = c.qtr_ord - 12
        )
        SELECT
            permno, fiscal_qtr, report_date,
            ttm_revenue, ttm_gross_profit, ttm_ebitda, ttm_ebit,
            ttm_net_income, ttm_eps, ttm_fcf, ttm_sbc, ttm_capex,
            CASE WHEN ttm_fcf IS NOT NULL AND ttm_sbc IS NOT NULL
                 THEN ttm_fcf - ttm_sbc END,
            gross_margin_pct, ebitda_margin_pct, net_margin_pct, fcf_margin_pct,
            shares_diluted_adj, shares_outstanding_adj, cash, debt,
            ttm_pretax_income, ttm_income_tax, avg_diluted_shares,
            invested_capital, avg_invested_capital, normalized_tax_rate,
            roic_pct, roic_3y_median_pct, fcf_conversion_pct,
            fcf_conversion_3y_median_pct, fcf_per_share, net_debt_ebitda,
            CASE WHEN gross_margin_pct IS NOT NULL AND prior_gross_margin_pct IS NOT NULL
                 THEN (gross_margin_pct - prior_gross_margin_pct) * 100 END,
            CASE WHEN ebitda_margin_pct IS NOT NULL AND prior_ebitda_margin_pct IS NOT NULL
                 THEN (ebitda_margin_pct - prior_ebitda_margin_pct) * 100 END,
            CASE WHEN fcf_per_share IS NOT NULL AND prior_fcf_per_share IS NOT NULL
                       AND prior_fcf_per_share != 0
                 THEN (fcf_per_share - prior_fcf_per_share)
                      / ABS(prior_fcf_per_share) * 100 END,
            CASE WHEN fcf_per_share > 0 AND prior3_fcf_per_share > 0
                 THEN (POWER(fcf_per_share / prior3_fcf_per_share, 1.0 / 3.0) - 1) * 100 END,
            CASE WHEN shares_outstanding_adj IS NOT NULL
                       AND prior_shares_outstanding IS NOT NULL
                       AND prior_shares_outstanding != 0
                 THEN (shares_outstanding_adj - prior_shares_outstanding)
                      / ABS(prior_shares_outstanding) * 100 END,
            CASE WHEN shares_outstanding_adj > 0 AND prior3_shares_outstanding > 0
                 THEN (POWER(shares_outstanding_adj / prior3_shares_outstanding, 1.0 / 3.0) - 1) * 100 END,
            CASE WHEN revenue IS NOT NULL AND prior_qtr_revenue IS NOT NULL
                       AND prior_qtr_revenue != 0
                 THEN (revenue - prior_qtr_revenue) / ABS(prior_qtr_revenue) * 100 END,
            CASE WHEN eps_diluted_adj IS NOT NULL AND prior_qtr_eps IS NOT NULL
                       AND prior_qtr_eps != 0
                 THEN (eps_diluted_adj - prior_qtr_eps) / ABS(prior_qtr_eps) * 100 END,
            quarter_count, ?, CURRENT_TIMESTAMP
        FROM enriched
    """, [permno, permno, TTM_CALCULATION_VERSION])


def refresh_all_ttm() -> None:
    """Refresh TTM cache for every company in the database."""
    con = get_conn()
    permnos = [r[0] for r in con.execute("SELECT permno FROM companies").fetchall()]
    for p in permnos:
        refresh_ttm(p)
    log.info("TTM refreshed for %d companies.", len(permnos))


def backfill_ttm_history() -> None:
    """Populate or repair fundamentals_ttm for companies with existing fundamentals."""
    con = get_conn()
    if con.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0] == 0:
        return
    permnos = [
        r[0] for r in con.execute(
            """
            SELECT f.permno
            FROM (SELECT permno, COUNT(*) n FROM fundamentals GROUP BY permno) f
            LEFT JOIN (
                SELECT permno, COUNT(*) n, MIN(calculation_version) min_version,
                       MAX(calculation_version) max_version
                FROM fundamentals_ttm GROUP BY permno
            ) t USING (permno)
            LEFT JOIN ttm_cache c USING (permno)
            WHERE t.permno IS NULL OR t.n != f.n
               OR t.min_version IS DISTINCT FROM ?
               OR t.max_version IS DISTINCT FROM ?
               OR c.calculation_version IS DISTINCT FROM ?
            ORDER BY f.permno
            """, [TTM_CALCULATION_VERSION, TTM_CALCULATION_VERSION,
                  TTM_CALCULATION_VERSION]
        ).fetchall()
    ]
    for permno in permnos:
        refresh_ttm_history(permno)
        _refresh_ttm_cache_from_history(permno)
    if permnos:
        log.info("TTM history backfilled for %d companies (calculation v%d).",
                 len(permnos), TTM_CALCULATION_VERSION)


# ---------------------------------------------------------------------------
# Chart series
# ---------------------------------------------------------------------------

def _pct_change(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return (curr - prev) / abs(prev) * 100


def _attach_growth(rows: list[dict], value_key: str = "value") -> list[dict]:
    for i, row in enumerate(rows):
        row["qoq_pct"] = _pct_change(row.get(value_key), rows[i - 1].get(value_key)) if i >= 1 else None
        row["yoy_pct"] = _pct_change(row.get(value_key), rows[i - 4].get(value_key)) if i >= 4 else None
    return rows


def get_quarterly_series(permno: int, n_quarters: int | None = None) -> list[dict]:
    """Return quarterly fundamentals for bar-chart rendering. None = all available."""
    con = get_conn()
    limit_clause = f"LIMIT {n_quarters}" if n_quarters else ""
    rows = con.execute(f"""
        SELECT
            fiscal_qtr, report_date, revenue, gross_profit, ebitda,
            net_income, fcf, sbc, capex, eps_diluted, shares_diluted,
            COALESCE(shares_outstanding, shares_diluted) AS shares_outstanding,
            cash, debt,
            fcf - sbc AS fcf_ex_sbc,
            gross_profit / NULLIF(revenue,0) * 100  AS gross_margin_pct,
            ebitda       / NULLIF(revenue,0) * 100  AS ebitda_margin_pct,
            net_income   / NULLIF(revenue,0) * 100  AS net_margin_pct,
            fcf          / NULLIF(revenue,0) * 100  AS fcf_margin_pct
        FROM fundamentals
        WHERE permno = ?
        ORDER BY fiscal_qtr DESC
        {limit_clause}
    """, [permno]).fetchall()

    cols = [
        "fiscal_qtr","report_date","revenue","gross_profit","ebitda",
        "net_income","fcf","sbc","capex","eps_diluted","shares_diluted",
        "shares_outstanding","cash","debt",
        "fcf_ex_sbc","gross_margin_pct","ebitda_margin_pct","net_margin_pct","fcf_margin_pct"
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_ttm_chart_series(permno: int, metric: str, mode: str = "grid") -> list[dict]:
    """Return historical TTM chart rows. Grid is capped; detail is full history."""
    col = TTM_CHART_COLUMNS.get(metric)
    if not col:
        return []
    con = get_conn()
    margin_col = TTM_MARGIN_COLUMNS.get(metric, "NULL")
    limit_clause = f"LIMIT {settings.GRID_QUARTERS}" if mode == "grid" else ""
    rows = con.execute(f"""
        SELECT fiscal_qtr, report_date, {col} AS value, {margin_col} AS margin,
               fcf_conversion_pct, fcf_per_share, net_debt_ebitda
        FROM (
            SELECT fiscal_qtr, report_date, {col}, {margin_col},
                   fcf_conversion_pct, fcf_per_share, net_debt_ebitda
            FROM fundamentals_ttm
            WHERE permno = ? AND quarter_count = ?
            ORDER BY fiscal_qtr DESC
            {limit_clause}
        ) s
        ORDER BY fiscal_qtr ASC
    """, [permno, settings.TTM_WINDOW]).fetchall()
    data = [
        {"fiscal_qtr": r[0], "report_date": str(r[1]), "value": r[2], "margin": r[3],
         "fcf_conversion_pct": r[4], "fcf_per_share": r[5], "net_debt_ebitda": r[6]}
        for r in rows
    ]
    return _attach_growth(data)


# Flow metrics are summed across the fiscal year; stock metrics take the
# final reported quarter's value.
_ANNUAL_FLOW_COLUMNS = {
    "revenue": "revenue",
    "ebitda": "ebitda",
    "net_income": "net_income",
    "fcf": "fcf",
    "eps": "eps_diluted",
    "sbc": "sbc",
    "capex": "capex",
}
_ANNUAL_STOCK_COLUMNS = {
    "shares": "COALESCE(shares_outstanding, shares_diluted)",
    "cash": "cash",
    "debt": "debt",
}
# Margin numerator per metric; annual margin = SUM(numerator) / SUM(revenue) * 100
_ANNUAL_MARGIN_NUMERATORS = {
    "revenue": "gross_profit", "ebitda": "ebitda",
    "net_income": "net_income", "fcf": "fcf",
}


def get_annual_series(permno: int, metric: str) -> list[dict]:
    """Return fiscal-year aggregates for the period toggle's Annual view.

    Only years with all four quarters reported are included, so the most
    recent partial year never shows a misleading low bar.
    """
    con = get_conn()
    if metric in _ANNUAL_FLOW_COLUMNS:
        col = _ANNUAL_FLOW_COLUMNS[metric]
        agg = f"SUM({col})"
    elif metric in _ANNUAL_STOCK_COLUMNS:
        col = _ANNUAL_STOCK_COLUMNS[metric]
        agg = f"LAST({col} ORDER BY fiscal_qtr)"
    else:
        return []
    margin_num = _ANNUAL_MARGIN_NUMERATORS.get(metric)
    margin_sel = f"SUM({margin_num}) / NULLIF(SUM(revenue),0) * 100" if margin_num else "NULL"
    rows = con.execute(f"""
        SELECT
            SUBSTR(fiscal_qtr, 1, 4) AS fiscal_year,
            MAX(report_date) AS report_date,
            {agg} AS value,
            {margin_sel} AS margin
        FROM fundamentals
        WHERE permno = ? AND fiscal_qtr IS NOT NULL
        GROUP BY fiscal_year
        HAVING COUNT(*) = ?
        ORDER BY fiscal_year ASC
    """, [permno, settings.TTM_WINDOW]).fetchall()
    data = [
        {"fiscal_qtr": r[0], "report_date": str(r[1]), "value": r[2], "margin": r[3]}
        for r in rows
    ]
    for i, row in enumerate(data):
        row["qoq_pct"] = None
        row["yoy_pct"] = _pct_change(row.get("value"), data[i - 1].get("value")) if i >= 1 else None
    return data


def get_dividend_series(permno: int) -> list[dict]:
    """Quarterly dividend-per-share totals from raw ex-date events."""
    con = get_conn()
    rows = con.execute("""
        SELECT
            CAST(YEAR(ex_date) AS VARCHAR) || 'Q' || CAST(QUARTER(ex_date) AS VARCHAR) AS period,
            SUM(amount) AS amount
        FROM dividends
        WHERE permno = ?
        GROUP BY period
        ORDER BY period ASC
    """, [permno]).fetchall()
    return [{"fiscal_qtr": r[0], "value": round(r[1], 4)} for r in rows]


def get_dividend_metrics(permno: int) -> dict | None:
    """TTM dividends/share plus yield vs current price and payout vs TTM EPS.

    Returns None when no dividend event falls in the trailing twelve months —
    true non-payers and companies whose dividends were never backfilled look
    identical (scripts/backfill_dividends.py fills the latter).
    """
    con = get_conn()
    row = con.execute("""
        SELECT
            d.ttm_dps,
            CASE WHEN m.price_current > 0 AND NOT isnan(m.price_current)
                 THEN d.ttm_dps / m.price_current * 100 END AS div_yield_pct,
            CASE WHEN t.ttm_eps > 0 AND NOT isnan(t.ttm_eps)
                 THEN d.ttm_dps / t.ttm_eps * 100 END AS payout_pct
        FROM (
            SELECT SUM(amount) AS ttm_dps
            FROM dividends
            WHERE permno = ?
              AND ex_date >= CURRENT_DATE - INTERVAL 365 DAY
        ) d
        LEFT JOIN momentum  m ON m.permno = ?
        LEFT JOIN ttm_cache t ON t.permno = ?
        WHERE d.ttm_dps IS NOT NULL AND d.ttm_dps > 0
    """, [permno, permno, permno]).fetchone()
    if not row:
        return None
    return {"ttm_dps": row[0], "div_yield_pct": row[1], "payout_pct": row[2]}


def upsert_dividends(permno: int, events: list[tuple]) -> int:
    """Insert/replace dividend events: [(ex_date, amount), …]."""
    with write_section() as con:
        count = 0
        for ex_date, amount in events:
            if ex_date is None or amount is None or amount <= 0:
                continue
            con.execute(
                "INSERT OR REPLACE INTO dividends (permno, ex_date, amount) VALUES (?,?,?)",
                [permno, ex_date, float(amount)],
            )
            count += 1
        return count


def get_cash_debt_series(permno: int, mode: str = "grid") -> list[dict]:
    """Return quarterly cash and debt. Grid is capped; detail is full history."""
    con = get_conn()
    limit_clause = f"LIMIT {settings.CASH_DEBT_QUARTERS}" if mode == "grid" else ""
    rows = con.execute(f"""
        SELECT fiscal_qtr, report_date, cash, debt, net_debt_ebitda
        FROM (
            SELECT f.fiscal_qtr, f.report_date, f.cash, f.debt,
                   t.net_debt_ebitda
            FROM fundamentals f
            LEFT JOIN fundamentals_ttm t
              ON t.permno = f.permno AND t.fiscal_qtr = f.fiscal_qtr
            WHERE f.permno = ?
            ORDER BY fiscal_qtr DESC
            {limit_clause}
        ) s
        ORDER BY fiscal_qtr ASC
    """, [permno]).fetchall()
    return [
        {"fiscal_qtr": r[0], "report_date": str(r[1]), "cash": r[2], "debt": r[3],
         "net_debt_ebitda": r[4]}
        for r in rows
    ]


def get_share_count_series(permno: int, limit: int | None = None) -> list[dict]:
    """Split-normalized period-end share history with exact-horizon changes."""
    con = get_conn()
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    rows = con.execute(f"""
        SELECT fiscal_qtr, report_date, shares_outstanding,
               shares_outstanding_yoy_pct, shares_outstanding_3y_cagr_pct
        FROM (
            SELECT fiscal_qtr, report_date, shares_outstanding,
                   shares_outstanding_yoy_pct, shares_outstanding_3y_cagr_pct
            FROM fundamentals_ttm
            WHERE permno = ?
            ORDER BY fiscal_qtr DESC
            {limit_clause}
        ) s
        ORDER BY fiscal_qtr ASC
    """, [permno]).fetchall()
    return [{"fiscal_qtr": r[0], "report_date": str(r[1]), "value": r[2],
             "qoq_pct": None, "yoy_pct": r[3], "three_year_cagr_pct": r[4]}
            for r in rows]


def get_segment_chart_series(permno: int, mode: str = "grid") -> list[dict]:
    """Return segment revenue rows. Grid is capped at the last N quarters."""
    con = get_conn()
    limit_clause = f"LIMIT {settings.SEGMENT_QUARTERS}" if mode == "grid" else ""
    rows = con.execute(f"""
        WITH qtrs AS (
            SELECT DISTINCT fiscal_qtr
            FROM segment_revenue
            WHERE permno = ?
            ORDER BY fiscal_qtr DESC
            {limit_clause}
        )
        SELECT s.fiscal_qtr, s.segment, s.revenue
        FROM segment_revenue s
        JOIN qtrs q ON q.fiscal_qtr = s.fiscal_qtr
        WHERE s.permno = ?
        ORDER BY s.fiscal_qtr ASC, s.segment ASC
    """, [permno, permno]).fetchall()
    return [{"fiscal_qtr": r[0], "segment": r[1], "revenue": r[2]} for r in rows]


@serialized_write
def upsert_segment_revenue(permno: int, fiscal_qtr: str, segment: str,
                           revenue: float | None) -> None:
    """Insert/replace one segment-revenue row (manual or WRDS-sourced)."""
    con = get_conn()
    con.execute(
        "INSERT OR REPLACE INTO segment_revenue (permno, fiscal_qtr, segment, revenue) "
        "VALUES (?,?,?,?)",
        [permno, fiscal_qtr, segment, revenue],
    )


def get_segment_revenue(permno: int) -> list[dict]:
    """Stacked segment revenue for last 8 quarters."""
    con = get_conn()
    rows = con.execute("""
        SELECT fiscal_qtr, segment, revenue
        FROM segment_revenue
        WHERE permno = ?
        ORDER BY fiscal_qtr DESC
        LIMIT 64
    """, [permno]).fetchall()
    return [{"qtr": r[0], "segment": r[1], "revenue": r[2]} for r in rows]


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def get_company_ttm(permno: int) -> dict | None:
    """Fetch the cached TTM row for a single company."""
    con = get_conn()
    cols = [
        "permno", "as_of_date", "ttm_revenue", "ttm_gross_profit",
        "ttm_ebitda", "ttm_ebit", "ttm_net_income", "ttm_eps", "ttm_fcf",
        "ttm_sbc", "ttm_capex", "gross_margin_pct", "ebitda_margin_pct",
        "fcf_margin_pct", "fcf_ex_sbc", "ttm_pretax_income",
        "ttm_income_tax", "avg_diluted_shares", "shares_outstanding",
        "invested_capital", "avg_invested_capital", "normalized_tax_rate",
        "roic_pct", "roic_3y_median_pct", "fcf_conversion_pct",
        "fcf_conversion_3y_median_pct", "fcf_per_share", "net_debt_ebitda",
        "gross_margin_yoy_bps", "ebitda_margin_yoy_bps",
        "fcf_per_share_yoy_pct", "fcf_per_share_3y_cagr_pct",
        "shares_outstanding_yoy_pct", "shares_outstanding_3y_cagr_pct",
        "qtr_revenue_yoy_pct", "qtr_eps_yoy_pct", "calculation_version",
        "updated_at",
    ]
    row = con.execute(
        f"SELECT {', '.join(cols)} FROM ttm_cache WHERE permno = ?", [permno]
    ).fetchone()
    if not row:
        return None
    return dict(zip(cols, row))


def get_yoy_growth(permno: int) -> dict | None:
    """Compare latest quarter revenue/earnings to same quarter prior year."""
    con = get_conn()
    rows = con.execute("""
        SELECT fiscal_qtr, revenue, net_income, eps_diluted
        FROM fundamentals
        WHERE permno = ?
        ORDER BY fiscal_qtr DESC
        LIMIT 8
    """, [permno]).fetchall()
    if len(rows) < 5:
        return None
    latest = rows[0]
    latest_qtr_num = latest[0][-1] if latest[0] else None
    if not latest_qtr_num:
        return None
    prior = None
    for r in rows[1:]:
        if r[0] and r[0][-1] == latest_qtr_num:
            prior = r
            break
    if not prior:
        return None
    return {
        "rev_yoy_pct": _pct_change(latest[1], prior[1]),
        "ni_yoy_pct": _pct_change(latest[2], prior[2]),
        "eps_yoy_pct": _pct_change(latest[3], prior[3]),
        "latest_qtr": latest[0],
    }


def get_latest_balance(permno: int) -> dict | None:
    """Return latest quarter's balance sheet items for the summary strip."""
    con = get_conn()
    row = con.execute("""
        SELECT cash, debt, shares_diluted, shares_outstanding,
               cash - debt AS net_cash
        FROM fundamentals
        WHERE permno = ? AND cash IS NOT NULL
        ORDER BY fiscal_qtr DESC
        LIMIT 1
    """, [permno]).fetchone()
    if not row:
        return None
    return {"cash": row[0], "debt": row[1], "shares_diluted": row[2],
            "shares_outstanding": row[3], "net_cash": row[4]}
