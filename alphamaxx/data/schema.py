"""Schema DDL + additive migrations. Primary key everywhere: WRDS PERMNO
(survives ticker changes). All migrations are additive — existing data is
never dropped or rewritten."""

from __future__ import annotations

import logging

import duckdb

from alphamaxx.config import ensure_private_path, settings
from alphamaxx.data.db import get_conn

log = logging.getLogger(__name__)


def init_db() -> None:
    """
    Initialize schema. Run once on first boot.
    All TTM aggregates are pre-computed to keep query latency < 50ms.
    """
    ensure_private_path(settings.PARQUET_DIR, directory=True)
    con = get_conn()

    con.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            permno       INTEGER PRIMARY KEY,
            ticker       VARCHAR,
            name         VARCHAR,
            sector       VARCHAR,
            industry     VARCHAR,
            exchange     VARCHAR,
            in_watchlist BOOLEAN DEFAULT FALSE,
            in_portfolio BOOLEAN DEFAULT FALSE,
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Quarterly fundamentals from WRDS Compustat
    con.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals (
            permno       INTEGER REFERENCES companies(permno),
            fiscal_qtr   VARCHAR,   -- e.g. '2024Q4'
            report_date  DATE,
            revenue      DOUBLE,    -- millions USD
            cogs         DOUBLE,
            gross_profit DOUBLE,
            ebitda       DOUBLE,
            ebit         DOUBLE,
            net_income   DOUBLE,
            eps_diluted  DOUBLE,
            fcf          DOUBLE,    -- operating CF - capex
            sbc          DOUBLE,    -- stock-based compensation
            capex        DOUBLE,
            pretax_income DOUBLE,
            income_tax    DOUBLE,
            shareholders_equity DOUBLE,
            shares_outstanding DOUBLE, -- period-end shares, millions
            shares_diluted DOUBLE,  -- diluted EPS denominator, millions
            cash         DOUBLE,
            debt         DOUBLE,
            PRIMARY KEY (permno, fiscal_qtr)
        )
    """)

    # Pre-aggregated TTM values (refreshed on each ingestion run)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ttm_cache (
            permno           INTEGER PRIMARY KEY REFERENCES companies(permno),
            as_of_date       DATE,
            ttm_revenue      DOUBLE,
            ttm_gross_profit DOUBLE,
            ttm_ebitda       DOUBLE,
            ttm_ebit         DOUBLE,
            ttm_net_income   DOUBLE,
            ttm_eps          DOUBLE,
            ttm_fcf          DOUBLE,
            ttm_sbc          DOUBLE,
            ttm_capex        DOUBLE,
            gross_margin_pct DOUBLE,
            ebitda_margin_pct DOUBLE,
            fcf_margin_pct   DOUBLE,
            fcf_ex_sbc       DOUBLE,   -- owner earnings
            ttm_pretax_income DOUBLE,
            ttm_income_tax   DOUBLE,
            avg_diluted_shares DOUBLE,
            shares_outstanding DOUBLE,
            invested_capital DOUBLE,
            avg_invested_capital DOUBLE,
            normalized_tax_rate DOUBLE,
            roic_pct DOUBLE,
            roic_3y_median_pct DOUBLE,
            fcf_conversion_pct DOUBLE,
            fcf_conversion_3y_median_pct DOUBLE,
            fcf_per_share DOUBLE,
            net_debt_ebitda DOUBLE,
            gross_margin_yoy_bps DOUBLE,
            ebitda_margin_yoy_bps DOUBLE,
            fcf_per_share_yoy_pct DOUBLE,
            fcf_per_share_3y_cagr_pct DOUBLE,
            shares_outstanding_yoy_pct DOUBLE,
            shares_outstanding_3y_cagr_pct DOUBLE,
            qtr_revenue_yoy_pct DOUBLE,
            qtr_eps_yoy_pct DOUBLE,
            calculation_version INTEGER,
            updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Historical TTM layer used by chart grids and expanded detail views.
    con.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals_ttm (
            permno            INTEGER REFERENCES companies(permno),
            fiscal_qtr        VARCHAR,
            report_date       DATE,
            ttm_revenue       DOUBLE,
            ttm_gross_profit  DOUBLE,
            ttm_ebitda        DOUBLE,
            ttm_ebit          DOUBLE,
            ttm_net_income    DOUBLE,
            ttm_eps           DOUBLE,
            ttm_fcf           DOUBLE,
            ttm_sbc           DOUBLE,
            ttm_capex         DOUBLE,
            fcf_ex_sbc        DOUBLE,
            gross_margin_pct  DOUBLE,
            ebitda_margin_pct DOUBLE,
            net_margin_pct    DOUBLE,
            fcf_margin_pct    DOUBLE,
            shares_diluted    DOUBLE,
            shares_outstanding DOUBLE,
            cash              DOUBLE,
            debt              DOUBLE,
            ttm_pretax_income DOUBLE,
            ttm_income_tax    DOUBLE,
            avg_diluted_shares DOUBLE,
            invested_capital DOUBLE,
            avg_invested_capital DOUBLE,
            normalized_tax_rate DOUBLE,
            roic_pct DOUBLE,
            roic_3y_median_pct DOUBLE,
            fcf_conversion_pct DOUBLE,
            fcf_conversion_3y_median_pct DOUBLE,
            fcf_per_share DOUBLE,
            net_debt_ebitda DOUBLE,
            gross_margin_yoy_bps DOUBLE,
            ebitda_margin_yoy_bps DOUBLE,
            fcf_per_share_yoy_pct DOUBLE,
            fcf_per_share_3y_cagr_pct DOUBLE,
            shares_outstanding_yoy_pct DOUBLE,
            shares_outstanding_3y_cagr_pct DOUBLE,
            qtr_revenue_yoy_pct DOUBLE,
            qtr_eps_yoy_pct DOUBLE,
            quarter_count     INTEGER,
            calculation_version INTEGER,
            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (permno, fiscal_qtr)
        )
    """)

    # Weekly price history from yfinance
    con.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            permno     INTEGER REFERENCES companies(permno),
            price_date DATE,
            open       DOUBLE,
            high       DOUBLE,
            low        DOUBLE,
            close      DOUBLE,
            adj_close  DOUBLE,
            volume     BIGINT,
            split_factor DOUBLE DEFAULT 1.0,
            PRIMARY KEY (permno, price_date)
        )
    """)

    # Cash dividend events from yfinance (per-share amounts by ex-date)
    con.execute("""
        CREATE TABLE IF NOT EXISTS dividends (
            permno  INTEGER REFERENCES companies(permno),
            ex_date DATE,
            amount  DOUBLE,
            PRIMARY KEY (permno, ex_date)
        )
    """)

    # Raw split events. EPS normalization uses these to express old EPS on
    # the latest reported quarter's share basis.
    con.execute("""
        CREATE TABLE IF NOT EXISTS stock_splits (
            permno       INTEGER REFERENCES companies(permno),
            split_date   DATE,
            split_factor DOUBLE,
            PRIMARY KEY (permno, split_date)
        )
    """)

    # Derived momentum metrics (refreshed weekly)
    con.execute("""
        CREATE TABLE IF NOT EXISTS momentum (
            permno         INTEGER PRIMARY KEY REFERENCES companies(permno),
            as_of_date     DATE,
            price_current  DOUBLE,
            sma_50         DOUBLE,   -- ~50d SMA (SMA_50D_WEEKS weekly closes)
            sma_200        DOUBLE,   -- ~200d SMA (SMA_200D_WEEKS weekly closes)
            pct_from_52wh  DOUBLE,   -- % below 52-week high
            pct_from_200   DOUBLE,   -- % above/below the ~200d SMA
            rsi_14         DOUBLE,   -- 14-week Wilder RSI (weekly bars)
            pe_ttm         DOUBLE,
            ps_ttm         DOUBLE,
            ev_ebitda_ttm  DOUBLE,
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


    # Non-financial / custom KPIs (e.g. Robotaxi rides, Cloud backlog)
    con.execute("""
        CREATE TABLE IF NOT EXISTS custom_kpis (
            permno     INTEGER REFERENCES companies(permno),
            kpi_name   VARCHAR,
            period     VARCHAR,   -- e.g. '2024Q4'
            value      DOUBLE,
            unit       VARCHAR,   -- 'rides', 'billions USD', etc.
            PRIMARY KEY (permno, kpi_name, period)
        )
    """)

    # Segment revenue breakdown
    con.execute("""
        CREATE TABLE IF NOT EXISTS segment_revenue (
            permno      INTEGER REFERENCES companies(permno),
            fiscal_qtr  VARCHAR,
            segment     VARCHAR,
            revenue     DOUBLE,
            PRIMARY KEY (permno, fiscal_qtr, segment)
        )
    """)

    # Earnings call transcript metadata
    con.execute("""
        CREATE TABLE IF NOT EXISTS transcripts (
            permno        INTEGER REFERENCES companies(permno),
            earnings_date DATE,
            fiscal_qtr    VARCHAR,
            raw_text      VARCHAR,
            ai_summary    VARCHAR,
            sentiment     VARCHAR,
            PRIMARY KEY (permno, earnings_date)
        )
    """)

    # Durable queue for companies that need fundamentals/prices downloaded.
    # Manual ticker entries may not have a local PERMNO yet; the ingestion
    # worker resolves them against current US CRSP listings before download.
    con.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_queue (
            permno           INTEGER,
            ticker           VARCHAR NOT NULL,
            requested_ticker VARCHAR,
            name             VARCHAR,
            reason           VARCHAR,
            status           VARCHAR DEFAULT 'queued',
            queued_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at       TIMESTAMP,
            completed_at     TIMESTAMP,
            error            VARCHAR
        )
    """)
    _migrate_ingestion_queue_schema(con)

    # Durable cache of yfinance valuation ratios (PE / PEG), refreshed by the
    # price-updater background thread. Lets watchlist/portfolio pages render
    # without blocking on synchronous yfinance network calls.
    con.execute("""
        CREATE TABLE IF NOT EXISTS valuation_cache (
            ticker     VARCHAR PRIMARY KEY,
            pe         DOUBLE,
            peg        DOUBLE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Economic calendar — US macro events (CPI, FOMC, NFP…) and their
    # tiered AI/seed descriptions. Deterministic sha1 ids keep upserts
    # idempotent (DuckDB has no autoincrement). Append-only descriptions.
    con.execute("""
        CREATE TABLE IF NOT EXISTS econ_events (
            event_id    VARCHAR PRIMARY KEY,   -- sha1(source|external_id)
            source      VARCHAR NOT NULL,      -- 'forexfactory'
            external_id VARCHAR NOT NULL,
            title       VARCHAR NOT NULL,
            event_type  VARCHAR,               -- classifier key, e.g. 'cpi'
            importance  VARCHAR DEFAULT 'low', -- 'high' | 'med' | 'low'
            event_time  TIMESTAMP NOT NULL,    -- stored UTC
            forecast    VARCHAR,
            previous    VARCHAR,
            actual      VARCHAR,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS econ_descriptions (
            desc_id      VARCHAR PRIMARY KEY,   -- sha1(event_id|tier|generated_at)
            event_id     VARCHAR NOT NULL,      -- FK -> econ_events.event_id
            body         VARCHAR NOT NULL,      -- markdown
            tier         VARCHAR NOT NULL,      -- 'static'|'m1'|'w2'|'w1'|'manual'
            generated_by VARCHAR NOT NULL,      -- 'seed'|'gemini'|'anthropic'|'openai'
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    migrate(con)

    # One-way compatibility migration. Old versions stored splits on weekly
    # bars; clearing those markers prevents a later actual-date event from
    # being copied alongside the old Monday date on every startup.
    con.execute("""
        INSERT INTO stock_splits
        SELECT permno, price_date, split_factor
        FROM prices
        WHERE split_factor IS NOT NULL AND split_factor != 1.0
        ON CONFLICT (permno, split_date) DO NOTHING
    """)
    con.execute("""
        UPDATE prices SET split_factor = 1.0
        WHERE split_factor IS NOT NULL AND split_factor != 1.0
    """)

    from alphamaxx.data.fundamentals import backfill_ttm_history
    backfill_ttm_history()
    log.info("Schema initialized at %s", settings.DB_PATH)


def _column_exists(con: duckdb.DuckDBPyConnection, table: str, col: str) -> bool:
    existing = {
        row[1].lower()
        for row in con.execute(f"PRAGMA table_info('{table}')").fetchall()
    }
    return col.lower() in existing


def migrate(con: duckdb.DuckDBPyConnection) -> None:
    """Additive column/table migrations for databases created by older builds."""
    for table, columns in {
        "companies": [
            ("fundamentals_count", "INTEGER", "0"),
            ("prices_count", "INTEGER", "0"),
            ("last_ingested_at", "TIMESTAMP", "NULL"),
            ("ingestion_status", "VARCHAR", "'pending'"),
            ("in_portfolio", "BOOLEAN", "FALSE"),
            ("website", "VARCHAR", None),
            ("employees", "INTEGER", None),
            ("ceo", "VARCHAR", None),
        ],
        "fundamentals": [
            ("capex", "DOUBLE", None),
            ("pretax_income", "DOUBLE", None),
            ("income_tax", "DOUBLE", None),
            ("shareholders_equity", "DOUBLE", None),
            ("shares_outstanding", "DOUBLE", None),
        ],
        "ttm_cache": [
            ("ttm_capex", "DOUBLE", None),
            ("ttm_pretax_income", "DOUBLE", None),
            ("ttm_income_tax", "DOUBLE", None),
            ("avg_diluted_shares", "DOUBLE", None),
            ("shares_outstanding", "DOUBLE", None),
            ("invested_capital", "DOUBLE", None),
            ("avg_invested_capital", "DOUBLE", None),
            ("normalized_tax_rate", "DOUBLE", None),
            ("roic_pct", "DOUBLE", None),
            ("roic_3y_median_pct", "DOUBLE", None),
            ("fcf_conversion_pct", "DOUBLE", None),
            ("fcf_conversion_3y_median_pct", "DOUBLE", None),
            ("fcf_per_share", "DOUBLE", None),
            ("net_debt_ebitda", "DOUBLE", None),
            ("gross_margin_yoy_bps", "DOUBLE", None),
            ("ebitda_margin_yoy_bps", "DOUBLE", None),
            ("fcf_per_share_yoy_pct", "DOUBLE", None),
            ("fcf_per_share_3y_cagr_pct", "DOUBLE", None),
            ("shares_outstanding_yoy_pct", "DOUBLE", None),
            ("shares_outstanding_3y_cagr_pct", "DOUBLE", None),
            ("qtr_revenue_yoy_pct", "DOUBLE", None),
            ("qtr_eps_yoy_pct", "DOUBLE", None),
            ("calculation_version", "INTEGER", None),
        ],
        "fundamentals_ttm": [
            ("ttm_capex", "DOUBLE", None),
            ("shares_outstanding", "DOUBLE", None),
            ("ttm_pretax_income", "DOUBLE", None),
            ("ttm_income_tax", "DOUBLE", None),
            ("avg_diluted_shares", "DOUBLE", None),
            ("invested_capital", "DOUBLE", None),
            ("avg_invested_capital", "DOUBLE", None),
            ("normalized_tax_rate", "DOUBLE", None),
            ("roic_pct", "DOUBLE", None),
            ("roic_3y_median_pct", "DOUBLE", None),
            ("fcf_conversion_pct", "DOUBLE", None),
            ("fcf_conversion_3y_median_pct", "DOUBLE", None),
            ("fcf_per_share", "DOUBLE", None),
            ("net_debt_ebitda", "DOUBLE", None),
            ("gross_margin_yoy_bps", "DOUBLE", None),
            ("ebitda_margin_yoy_bps", "DOUBLE", None),
            ("fcf_per_share_yoy_pct", "DOUBLE", None),
            ("fcf_per_share_3y_cagr_pct", "DOUBLE", None),
            ("shares_outstanding_yoy_pct", "DOUBLE", None),
            ("shares_outstanding_3y_cagr_pct", "DOUBLE", None),
            ("qtr_revenue_yoy_pct", "DOUBLE", None),
            ("qtr_eps_yoy_pct", "DOUBLE", None),
            ("calculation_version", "INTEGER", None),
        ],
        "valuation_cache": [
            ("forward_pe", "DOUBLE", None),
        ],
    }.items():
        for col, dtype, default in columns:
            if _column_exists(con, table, col):
                continue
            default_clause = f" DEFAULT {default}" if default else ""
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}{default_clause}")
            log.info("Migration: added %s.%s", table, col)


def _migrate_ingestion_queue_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Move older PERMNO-primary-key queue tables to ticker-first queue rows."""
    info = con.execute("PRAGMA table_info('ingestion_queue')").fetchall()
    cols = {row[1].lower(): row for row in info}
    if "requested_ticker" in cols and not cols.get("permno", (None,) * 6)[5]:
        return

    con.execute("""
        CREATE TABLE ingestion_queue_new (
            permno           INTEGER,
            ticker           VARCHAR NOT NULL,
            requested_ticker VARCHAR,
            name             VARCHAR,
            reason           VARCHAR,
            status           VARCHAR DEFAULT 'queued',
            queued_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at       TIMESTAMP,
            completed_at     TIMESTAMP,
            error            VARCHAR
        )
    """)
    if "ticker" in cols:
        con.execute("""
            INSERT INTO ingestion_queue_new (
                permno, ticker, requested_ticker, name, reason, status,
                queued_at, started_at, completed_at, error
            )
            SELECT
                permno,
                UPPER(ticker),
                UPPER(ticker),
                NULL,
                reason,
                status,
                queued_at,
                started_at,
                completed_at,
                error
            FROM ingestion_queue
            WHERE ticker IS NOT NULL AND ticker != ''
        """)
    con.execute("DROP TABLE ingestion_queue")
    con.execute("ALTER TABLE ingestion_queue_new RENAME TO ingestion_queue")
