"""Public query API for the AlphaMaxx data layer.

Routes and services import from here (or the domain modules directly);
raw SQL never appears outside this package.
"""

from alphamaxx.data.db import get_conn
from alphamaxx.data.schema import init_db, migrate
from alphamaxx.data.companies import (
    add_company,
    backfill_ingestion_status,
    companies_with_transcripts,
    get_companies_with_data,
    get_company_by_ticker,
    get_company_header,
    get_company_profile,
    get_ingestion_summary,
    get_portfolio_summary,
    get_transcripts,
    get_watchlist_summary,
    refresh_ingestion_status,
    search_companies,
    set_portfolio,
    set_watchlist,
    tracked_companies,
    update_company_profile,
    upsert_company_identity,
    watchlist_companies,
)
from alphamaxx.data.fundamentals import (
    TTM_CHART_COLUMNS,
    TTM_CALCULATION_VERSION,
    backfill_ttm_history,
    get_annual_series,
    get_cash_debt_series,
    get_company_ttm,
    get_dividend_metrics,
    get_dividend_series,
    get_latest_balance,
    get_quarterly_series,
    get_segment_chart_series,
    get_share_count_series,
    get_segment_revenue,
    get_ttm_chart_series,
    get_yoy_growth,
    refresh_all_ttm,
    refresh_ttm,
    refresh_ttm_history,
    upsert_dividends,
    upsert_segment_revenue,
)
from alphamaxx.data.momentum import get_company_momentum
from alphamaxx.data.prices import (
    get_correlation_matrix,
    get_latest_price_date,
    get_pe_history,
    get_price_history,
    upsert_stock_splits,
)
from alphamaxx.data.queue import (
    attach_ingestion_queue_company,
    count_pending_queue,
    clear_completed_ingestion_queue,
    clear_failed_ingestion_queue,
    relabel_failed_queue_errors,
    delete_ingestion_queue_item,
    enqueue_ingestion,
    enqueue_missing_ingestion,
    enqueue_ticker,
    get_ingestion_queue,
    get_queued_ingestions,
    mark_ingestion_queue_status,
)
from alphamaxx.data.valuation import get_valuation_cache, upsert_valuation_cache
from alphamaxx.data.econ import (
    add_econ_description,
    clear_econ_data,
    current_econ_description,
    current_econ_descriptions_for,
    econ_events_between,
    econ_tiers_present,
    future_econ_events,
    get_econ_event,
    upsert_econ_event,
)

__all__ = [
    "get_conn", "init_db", "migrate",
    "add_company", "backfill_ingestion_status", "companies_with_transcripts",
    "get_companies_with_data",
    "get_company_by_ticker", "get_company_header", "get_company_profile",
    "get_ingestion_summary",
    "get_portfolio_summary", "get_transcripts", "get_watchlist_summary",
    "refresh_ingestion_status", "search_companies",
    "set_portfolio", "set_watchlist", "tracked_companies",
    "update_company_profile", "upsert_company_identity", "watchlist_companies",
    "TTM_CHART_COLUMNS", "TTM_CALCULATION_VERSION", "backfill_ttm_history", "get_annual_series",
    "get_cash_debt_series", "get_company_ttm", "get_dividend_metrics",
    "get_dividend_series",
    "get_latest_balance", "get_quarterly_series",
    "get_segment_chart_series", "get_segment_revenue", "get_share_count_series", "get_ttm_chart_series",
    "get_yoy_growth", "refresh_all_ttm", "refresh_ttm", "refresh_ttm_history",
    "upsert_dividends", "upsert_segment_revenue",
    "get_company_momentum",
    "get_correlation_matrix", "get_latest_price_date",
    "get_pe_history", "get_price_history", "upsert_stock_splits",
    "attach_ingestion_queue_company", "count_pending_queue",
    "clear_completed_ingestion_queue",
    "clear_failed_ingestion_queue", "relabel_failed_queue_errors",
    "delete_ingestion_queue_item",
    "enqueue_ingestion", "enqueue_missing_ingestion", "enqueue_ticker",
    "get_ingestion_queue", "get_queued_ingestions", "mark_ingestion_queue_status",
    "get_valuation_cache", "upsert_valuation_cache",
    "add_econ_description", "clear_econ_data", "current_econ_description",
    "current_econ_descriptions_for", "econ_events_between",
    "econ_tiers_present", "future_econ_events", "get_econ_event",
    "upsert_econ_event",
]
