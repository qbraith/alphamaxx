"""Stock insights pages: header, summary boxes, 12-chart grid, chart API."""

from __future__ import annotations

from fasthtml.common import H2, H4, Button, Div, P, Span

from alphamaxx.data import (
    get_annual_series,
    get_cash_debt_series,
    get_company_by_ticker,
    get_company_header,
    get_company_momentum,
    get_company_profile,
    get_dividend_series,
    get_pe_history,
    get_price_history,
    get_quarterly_series,
    get_segment_chart_series,
    get_share_count_series,
    get_ttm_chart_series,
    get_valuation_cache,
)
from alphamaxx.data.fundamentals import _attach_growth
from alphamaxx.web.charts import (
    METRIC_META,
    area_chart,
    bar_chart,
    cash_debt_chart,
    metric_title,
    pe_cloud_chart,
    segment_chart,
    ttm_bar_chart,
)
from alphamaxx.web.components import (
    card, color_cls, fmt_num, fmt_pct, fmt_price, fmt_ratio,
    metric_row, section_title, tape_chip,
)
from alphamaxx.web.shell import full_page
from alphamaxx.web.tables import (
    cash_debt_detail_table,
    chart_detail_table,
    detail_table,
    segment_detail_table,
)


# ---------------------------------------------------------------------------
# Composers
# ---------------------------------------------------------------------------

def _fmt_updated(ts) -> str:
    """Render the last-ingested timestamp as 'Last Updated: mm/dd/yyyy'."""
    if not ts:
        return "Last Updated: —"
    try:
        return "Last Updated: " + ts.strftime("%m/%d/%Y")
    except AttributeError:
        # string fallback (YYYY-MM-DD... -> mm/dd/yyyy)
        s = str(ts)[:10]
        try:
            y, m, d = s.split("-")
            return f"Last Updated: {m}/{d}/{y}"
        except ValueError:
            return "Last Updated: " + s


def ticker_tape(ticker: str, name: str, exchange: str, momentum: dict | None,
                last_ingested=None, forward_pe: float | None = None):
    """One-line terminal header with standard market metrics only."""
    price = momentum.get("price_current") if momentum else None
    pct_52 = momentum.get("pct_from_52wh") if momentum else None
    pct_200 = momentum.get("pct_from_200") if momentum else None
    rsi = momentum.get("rsi_14") if momentum else None
    chips = [
        tape_chip("Δ200d", fmt_pct(pct_200), color_cls(pct_200)),
        tape_chip("52wH", fmt_pct(pct_52), color_cls(pct_52)),
        tape_chip("RSI 14W", fmt_num(rsi, 0)),
        tape_chip("Fwd P/E", fmt_ratio(forward_pe)),
    ]
    return Div(
        Span(ticker, cls="tape-ticker"),
        Span(name, cls="tape-name"),
        Span(exchange or "", cls="tape-exchange"),
        *chips,
        Span(_fmt_updated(last_ingested), cls="tape-updated"),
        Span(fmt_price(price), cls="tape-price"),
        cls="ticker-tape",
    )

PERIODS = [("ttm", "TTM"), ("quarterly", "Qtrly"), ("annual", "Annual")]


def period_toggles(ticker: str, active: str = "ttm"):
    return Div(
        *[Button(label,
                 cls="toggle-btn active" if key == active else "toggle-btn",
                 hx_get=f"/stock/{ticker}/charts?period={key}",
                 hx_target="#insights-section",
                 hx_swap="outerHTML")
          for key, label in PERIODS],
        cls="period-toggles"
    )


def twelve_pane_grid(permno: int, ticker: str, period: str = "ttm"):
    panes = [
        ("price-chart",       f"/api/chart/{permno}/price",       "Price",              "price"),
        ("revenue-chart",     f"/api/chart/{permno}/revenue",     "Revenue",            "revenue"),
        ("segment-chart",     f"/api/chart/{permno}/segments",    "Revenue By Segment", "segments"),
        ("ebitda-chart",      f"/api/chart/{permno}/ebitda",      "EBITDA",             "ebitda"),
        ("ni-chart",          f"/api/chart/{permno}/net_income",  "Net Income",         "net_income"),
        ("fcf-chart",         f"/api/chart/{permno}/fcf",         "Free Cash Flow",     "fcf"),
        ("eps-chart",         f"/api/chart/{permno}/eps",         "EPS",                "eps"),
        ("cash-debt-chart",   f"/api/chart/{permno}/cash_debt",   "Cash & Debt",        "cash_debt"),
        ("dividends-chart",   f"/api/chart/{permno}/dividends",   "Dividends",          "dividends"),
        ("shares-chart",      f"/api/chart/{permno}/shares",      "Shares Outstanding", "shares"),
        ("sbc-chart",         f"/api/chart/{permno}/sbc",         "Stock-Based Comp",   "sbc"),
        ("capex-chart",       f"/api/chart/{permno}/capex",       "CAPEX",              "capex"),
    ]
    pane_divs = []
    for i, (chart_id, endpoint, title, metric) in enumerate(panes):
        # Light stagger to avoid a thundering herd of 12 simultaneous XHRs.
        delay = i * 60
        trigger = f"intersect once delay:{delay}ms"
        endpoint = f"{endpoint}?period={period}"
        detail_url = f"/stock/{ticker}/chart/{metric}?period={period}"
        pane_divs.append(
            Div(
                Div(
                    H4(title, cls="chart-title"),
                    Button("⤢", cls="chart-expand-btn",
                           hx_get=detail_url,
                           hx_target="#chart-modal-body",
                           hx_swap="innerHTML",
                           hx_on__after_request="document.getElementById('chart-modal').style.display='flex';"),
                    cls="chart-pane-header"
                ),
                Div(id=f"{chart_id}-container", cls="chart-container",
                    hx_get=endpoint, hx_trigger=trigger, hx_swap="innerHTML"),
                cls="card chart-pane",
                onclick="if(!event.target.closest('.chart-expand-btn')) this.querySelector('.chart-expand-btn').click();",
            )
        )
    return Div(*pane_divs, cls="chart-grid-12")


def insights_section(permno: int, ticker: str, period: str = "ttm"):
    modal = Div(
        Div(
            Div(
                Button("X", cls="modal-close chart-modal-close",
                       onclick="document.getElementById('chart-modal').style.display='none';document.getElementById('chart-modal-body').innerHTML='';"),
                cls="modal-header"
            ),
            Div(id="chart-modal-body", cls="modal-body"),
            cls="modal-content chart-detail-modal-content"
        ),
        id="chart-modal", cls="chart-modal",
        style="display:none;",
        onclick="if(event.target===this){this.style.display='none';document.getElementById('chart-modal-body').innerHTML='';}"
    )
    return Div(
        modal,
        Div(
            section_title("Fundamentals Grid"),
            period_toggles(ticker, period),
            cls="insights-header"
        ),
        twelve_pane_grid(permno, ticker, period),
        id="insights-section",
    )


def sub_modules(permno: int, ticker: str):
    profile = get_company_profile(permno) or {}

    # Only render facts we actually have — no hardcoded em-dashes.
    facts = [
        ("Sector", profile.get("sector")),
        ("Industry", profile.get("industry")),
        ("Exchange", profile.get("exchange")),
        ("CEO", profile.get("ceo")),
        ("Employees", f"{profile['employees']:,}" if profile.get("employees") else None),
        ("Website", profile.get("website")),
    ]
    fact_rows = [metric_row(k, v) for k, v in facts if v]
    info_cards = [card(*fact_rows[i:i + 2]) for i in range(0, len(fact_rows), 2)]

    return Div(
        section_title("Valuation — P/E Cloud (10yr)"),
        card(
            Div(id="pe-cloud-container", cls="chart-container",
                style="height:320px;",
                hx_get=f"/api/chart/{permno}/pe_cloud",
                hx_trigger="load", hx_swap="innerHTML"),
            cls="pe-cloud-card"
        ),
        section_title("Company Info") if info_cards else "",
        Div(*info_cards, cls="company-info-grid") if info_cards else "",
        cls="sub-modules"
    )


def stock_page(permno: int, ticker: str):
    """Full stock insights page content."""
    momentum = get_company_momentum(permno)
    valuation = get_valuation_cache([ticker]).get(ticker, {})
    forward_pe = valuation.get("forward_pe")

    header = get_company_header(permno) or {}
    name = header.get("name") or ticker
    exchange = header.get("exchange") or ""
    last_ingested = header.get("last_ingested_at")

    return Div(
        ticker_tape(ticker, name, exchange, momentum, last_ingested,
                    forward_pe=forward_pe),
        insights_section(permno, ticker),
        sub_modules(permno, ticker),
        id="page-content", cls="page-content"
    )


# ---------------------------------------------------------------------------
# Grid chart handlers
# ---------------------------------------------------------------------------

# Metrics that render as a single bar series and honor the period toggle.
_TTM_BAR_METRICS = {"revenue", "ebitda", "net_income", "fcf", "eps", "sbc", "capex"}

# fundamentals-table column per metric, for the Quarterly view
_QUARTERLY_COLUMNS = {
    "revenue": "revenue", "ebitda": "ebitda", "net_income": "net_income",
    "fcf": "fcf", "eps": "eps_diluted", "sbc": "sbc", "capex": "capex",
}
# margin-% column per metric for the Quarterly view (already computed by the query)
_QUARTERLY_MARGIN_COLUMNS = {
    "revenue": "gross_margin_pct", "ebitda": "ebitda_margin_pct",
    "net_income": "net_margin_pct", "fcf": "fcf_margin_pct",
}


def _period_rows(permno: int, metric: str, period: str, mode: str = "grid") -> list[dict]:
    """Series rows for one metric under the selected period view."""
    if period == "quarterly":
        col = _QUARTERLY_COLUMNS[metric]
        margin_col = _QUARTERLY_MARGIN_COLUMNS.get(metric)
        series = get_quarterly_series(permno, n_quarters=None if mode == "detail" else 80)
        series.reverse()
        rows = [{"fiscal_qtr": s["fiscal_qtr"], "value": s.get(col),
                 "margin": s.get(margin_col) if margin_col else None} for s in series]
        return _attach_growth(rows)
    if period == "annual":
        return get_annual_series(permno, metric)
    return get_ttm_chart_series(permno, metric, mode)


def _period_label(metric: str, period: str) -> str:
    base = METRIC_META[metric]["label"]
    if period == "quarterly":
        return base.replace("TTM", "Qtr")
    if period == "annual":
        return base.replace("TTM", "FY")
    return base


def _grid_ttm_bar(permno: int, metric: str, period: str = "ttm"):
    rows = _period_rows(permno, metric, period, "grid")
    if not rows:
        return P("No data for this view.", cls="muted-text")
    if period == "ttm":
        return ttm_bar_chart(f"{metric}-grid", rows, metric, "grid")
    meta = METRIC_META[metric]
    decimals = 2 if metric == "eps" else 0
    return bar_chart(
        f"{metric}-grid-{period}",
        [r["fiscal_qtr"] for r in rows],
        [round(r["value"], decimals) if r.get("value") is not None else None for r in rows],
        _period_label(metric, period), meta["color"], mode="grid",
    )


def _grid_price(permno: int):
    prices = get_price_history(permno, weeks=1560)  # 30 years
    if not prices:
        return P("No price data.", cls="muted-text")
    prices.reverse()
    labels = [p["date"][:10] for p in prices]
    data = [round(p["price"] or 0, 2) for p in prices]
    return area_chart("price-grid", labels, data, "Price ($)", mode="grid")


def _grid_segments(permno: int):
    segments = get_segment_chart_series(permno, "grid")
    if not segments:
        return P("No segment data available.", cls="muted-text")
    return segment_chart("segment-grid", segments, "grid")


def _grid_cash_debt(permno: int):
    rows = get_cash_debt_series(permno, "grid")
    return cash_debt_chart("cash-debt-grid", rows, "grid")


def _grid_shares(permno: int):
    rows = get_share_count_series(permno, 80)
    return bar_chart("shares-grid", [r["fiscal_qtr"] for r in rows],
                     [round(r["value"] or 0, 0) for r in rows],
                     "Shares Outstanding (M)", "#94A3B8", mode="grid")


def _grid_dividends(permno: int):
    rows = get_dividend_series(permno)
    if not rows:
        return P("No dividend history (or none paid).", cls="muted-text")
    meta = METRIC_META["dividends"]
    return bar_chart("dividends-grid", [r["fiscal_qtr"] for r in rows],
                     [r["value"] for r in rows],
                     meta["label"], meta["color"], mode="grid")


def _grid_pe_cloud(permno: int):
    return pe_cloud_chart("pe-cloud", get_pe_history(permno), "grid")


_GRID_SPECIAL = {
    "price": _grid_price,
    "segments": _grid_segments,
    "cash_debt": _grid_cash_debt,
    "shares": _grid_shares,
    "dividends": _grid_dividends,
    "pe_cloud": _grid_pe_cloud,
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register(app, rt):

    @rt("/stock/{ticker}")
    def get(request, ticker: str):
        # Exact ticker only: a fuzzy fallback can silently render a different
        # company whose name merely contains the string.
        company = get_company_by_ticker(ticker)
        if not company:
            content = Div(P(f"Ticker '{ticker}' not found.", cls="muted-text"),
                          id="page-content", cls="page-content")
        else:
            content = stock_page(company["permno"], company["ticker"])

        if 'hx-request' in request.headers:
            return content
        return full_page(content)

    @rt("/stock/{ticker}/charts")
    def get(request, ticker: str, period: str = "ttm"):
        if period not in {"ttm", "quarterly", "annual"}:
            period = "ttm"
        company = get_company_by_ticker(ticker)
        if not company:
            content = Div(P(f"Ticker '{ticker}' not found.", cls="muted-text"), id="insights-section")
        else:
            content = insights_section(company["permno"], company["ticker"], period)
        if 'hx-request' in request.headers:
            return content
        return full_page(stock_page(company["permno"], company["ticker"]) if company else content)

    @rt("/stock/{ticker}/chart/{metric}")
    def get(request, ticker: str, metric: str, period: str = "ttm"):
        if period not in {"ttm", "quarterly", "annual"}:
            period = "ttm"
        company = get_company_by_ticker(ticker)
        if not company:
            content = Div(P(f"Ticker '{ticker}' not found.", cls="muted-text"))
            return content if 'hx-request' in request.headers else full_page(content)

        permno = company["permno"]
        chart_id = f"detail-{metric}-chart"
        title = metric_title(metric)

        if metric == "price":
            prices = get_price_history(permno, weeks=1560)
            prices.reverse()
            labels = [p["date"][:10] for p in prices]
            values = [round(p["price"] or 0, 2) for p in prices]
            chart = area_chart(chart_id, labels, values, "Price ($)", mode="detail") if prices else Div(P("No price data.", cls="muted-text"), cls="chart-empty")
            table = detail_table(labels, [{"price": v} for v in values], ["price"], ["Price ($)"]) if prices else ""
        elif metric in _TTM_BAR_METRICS:
            rows = _period_rows(permno, metric, period, "detail")
            if not rows:
                chart = Div(P("No data for this view.", cls="muted-text"), cls="chart-empty")
                table = ""
            else:
                label = None if period == "ttm" else _period_label(metric, period)
                chart = ttm_bar_chart(chart_id, rows, metric, "detail", label=label)
                table = chart_detail_table(rows, metric, label=_period_label(metric, period))
        elif metric == "cash_debt":
            rows = get_cash_debt_series(permno, "detail")
            chart = cash_debt_chart(chart_id, rows, "detail")
            table = cash_debt_detail_table(rows)
        elif metric == "segments":
            rows = get_segment_chart_series(permno, "detail")
            chart = segment_chart(chart_id, rows, "detail")
            table = segment_detail_table(rows)
        elif metric == "shares":
            rows = get_share_count_series(permno)
            chart = ttm_bar_chart(chart_id, rows, "shares", "detail")
            table = chart_detail_table(rows, "shares")
        elif metric == "dividends":
            rows = get_dividend_series(permno)
            if rows:
                meta = METRIC_META["dividends"]
                chart = bar_chart(chart_id, [r["fiscal_qtr"] for r in rows],
                                  [r["value"] for r in rows],
                                  meta["label"], meta["color"], mode="detail")
                table = chart_detail_table(rows, "dividends")
            else:
                chart = Div(P("No dividend history (or none paid).", cls="muted-text"), cls="chart-empty")
                table = ""
        else:
            chart = Div(P("Detailed bar view is not available for this metric yet.", cls="muted-text"), cls="chart-empty")
            table = ""

        content = Div(
            Div(
                H2(title, cls="chart-detail-title"),
                cls="chart-detail-header",
            ),
            Div(chart, cls="chart-detail-plot"),
            Div(table, cls="table-wrapper detail-table-wrap") if table else "",
            id="chart-detail-content",
            cls="chart-detail-section",
        )
        if 'hx-request' in request.headers:
            return content
        return full_page(stock_page(company["permno"], company["ticker"]))

    @rt("/api/chart/{permno:int}/{metric}")
    def get(permno: int, metric: str, period: str = "ttm"):
        if period not in {"ttm", "quarterly", "annual"}:
            period = "ttm"
        if metric in _TTM_BAR_METRICS:
            return _grid_ttm_bar(permno, metric, period)
        handler = _GRID_SPECIAL.get(metric)
        if handler:
            return handler(permno)
        return P("Unknown chart metric.", cls="muted-text")
