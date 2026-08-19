"""Analysis tools: earnings calendar, transcripts, and manual DCF."""

from __future__ import annotations

from fasthtml.common import (
    H2, H3, H4, A, Button, Details, Div, Form, Input, Label, P,
    Span, Summary, Table, Tbody, Td, Th, Thead, Tr, to_xml,
)
from starlette.responses import HTMLResponse

from alphamaxx.data import (
    companies_with_transcripts,
    get_company_by_ticker,
    get_transcripts,
)
from alphamaxx.services.dcf import scenario_cones
from alphamaxx.services.earnings import fetch_earnings_two_weeks, get_today_str
from alphamaxx.web.charts import AMETHYST, RED, TEAL, multi_line_chart
from alphamaxx.web.components import card, fmt_price, section_title
from alphamaxx.web.shell import full_page


def _dcf_prefill(ticker: str) -> dict:
    """Return illustrative defaults; users must supply their own assumptions."""
    prefill = {"ticker": "", "name": "", "eps": 1.00, "growth": 0.0,
               "multiple": 10.0, "desired": 10.0, "growth_spread": 40.0,
               "multiple_spread": 15.0, "price": None, "basis": "EPS"}
    if not ticker:
        return prefill
    company = get_company_by_ticker(ticker)
    prefill["ticker"] = ticker.upper()
    prefill["name"] = (company or {}).get("name") or "Manual assumptions"
    return prefill


def _dcf_results(eps: float, growth_pct: float, multiple: float,
                 desired_pct: float, growth_spread_pct: float,
                 multiple_spread_pct: float):
    """Results rows + scenario-cone chart for one set of DCF inputs.

    Single source of truth: the math lives in services/dcf.py and the chart in
    web/charts.py — the Calculate button round-trips here via HTMX.
    """
    cones = scenario_cones(
        eps, growth_pct / 100, multiple, desired_pct / 100, years=5,
        spread=growth_spread_pct / 100,
        multiple_spread=multiple_spread_pct / 100,
    )
    base, upper, lower = cones["base"], cones["upper"], cones["lower"]
    labels = [f"Year {y}" for y in range(cones["years"] + 1)]
    chart = multi_line_chart("dcf-chart", labels, [
        {"label": "Lower", "data": lower["path"], "color": RED, "point_radius": 2},
        {"label": "Base", "data": base["path"], "color": AMETHYST,
         "fill": AMETHYST + "1A", "fill_to": "-1", "point_radius": 2},
        {"label": "Upper", "data": upper["path"], "color": TEAL, "point_radius": 2},
    ])
    return Div(
        Div(
            Div(Span("Base Terminal Value", cls="metric-label"), Span(f"${base['terminal_value']:,.2f}", cls="metric-value"), cls="metric-row"),
            Div(Span("Lower–Upper Range", cls="metric-label"), Span(f"${lower['terminal_value']:,.0f} – ${upper['terminal_value']:,.0f}", cls="metric-value"), cls="metric-row"),
            Div(Span("Discounted Base Value", cls="metric-label"), Span(f"${base['discounted_terminal_value']:,.2f}", cls="metric-value positive"), cls="metric-row"),
            Div(Span("Projected Metric CAGR", cls="metric-label"), Span(f"{base['projected_metric_cagr']:.1f}%" if base['projected_metric_cagr'] is not None else "—", cls="metric-value"), cls="metric-row"),
            cls="dcf-results"
        ),
        Div(chart, cls="chart-container dcf-chart-area"),
        id="dcf-results-area",
    )


def register(app, rt):

    @rt("/earnings-calendar")
    def get(request):
        weeks, refreshing = fetch_earnings_two_weeks()
        today_str = get_today_str()

        day_sections = []
        current_week_label = None
        for date_str, week_label, day_name, earnings in weeks:
            if week_label != current_week_label:
                current_week_label = week_label
                day_sections.append(H2(week_label, cls="section-title"))

            is_today = date_str == today_str
            header_cls = "earnings-day-header earnings-today" if is_today else "earnings-day-header"
            today_tag = Span(" — Today", cls="today-tag") if is_today else ""

            if earnings:
                table_rows = []
                for e in earnings:
                    time_cls = "time-badge time-pre" if e["time"] == "Pre-Market" else \
                               "time-badge time-after" if e["time"] == "After Hours" else "time-badge"
                    table_rows.append(Tr(
                        Td(Span(e["time"], cls=time_cls)),
                        Td(A(e["symbol"], href=f"/stock/{e['symbol']}",
                             hx_get=f"/stock/{e['symbol']}", hx_target="#page-content",
                             hx_swap="outerHTML", hx_push_url="true", cls="ticker-link")),
                        Td(e["name"]),
                        Td(e["market_cap"], cls="num"),
                        Td(e["eps_forecast"], cls="num"),
                        Td(e["last_year_eps"], cls="num"),
                        Td(e["num_estimates"], cls="num"),
                        Td(e["fiscal_qtr"]),
                    ))
                day_content = Div(
                    Table(
                        Thead(Tr(
                            Th("Time"), Th("Ticker"), Th("Company"), Th("Market Cap"),
                            Th("EPS Est"), Th("Last Yr EPS"), Th("# Est"), Th("Fiscal Qtr")
                        )),
                        Tbody(*table_rows),
                        cls="data-table"
                    ),
                    cls="table-wrapper"
                )
            else:
                day_content = P("No earnings scheduled.", cls="muted-text", style="padding:12px 0;")

            day_sections.append(Div(
                H3(day_name, today_tag, cls=header_cls),
                day_content,
                cls="earnings-day"
            ))

        # While a background fetch is filling the cache, show a note and
        # re-render shortly — the request itself never blocks on the network.
        refresh_bits = []
        if refreshing:
            refresh_bits = [
                P("Fetching latest earnings data…", cls="muted-text"),
                Div(hx_get="/earnings-calendar", hx_trigger="load delay:4s",
                    hx_target="#page-content", hx_swap="outerHTML"),
            ]

        content = Div(
            section_title("Earnings Calendar — This Week & Next"),
            *refresh_bits,
            *day_sections,
            id="page-content", cls="page-content"
        )
        if 'hx-request' in request.headers:
            return content
        return full_page(content, "earnings-calendar")

    @rt("/transcripts")
    def get(request):
        rows = companies_with_transcripts(limit=100)

        if rows:
            items = [
                Div(
                    A(f"{r['ticker']} — {r['name']}", href=f"/transcripts/{r['ticker']}",
                      hx_get=f"/transcripts/{r['ticker']}", hx_target="#page-content",
                      hx_swap="outerHTML", hx_push_url="true", cls="transcript-link"),
                    Span(f"{r['n_transcripts']} transcripts", cls="muted-text"),
                    cls="transcript-item"
                ) for r in rows
            ]
            body = Div(*items, cls="transcript-list")
        else:
            body = Div(
                P("No transcript data available yet.", cls="muted-text"),
                P("This page displays transcript records populated by a separately authorized local importer.", cls="muted-text"),
                cls="empty-section"
            )

        content = Div(
            section_title("Earnings Transcripts"),
            card(body),
            id="page-content", cls="page-content"
        )
        if 'hx-request' in request.headers:
            return content
        return full_page(content, "transcripts")

    @rt("/transcripts/{ticker}")
    def get(request, ticker: str):
        company = get_company_by_ticker(ticker)
        if not company:
            content = Div(P(f"'{ticker}' not found.", cls="muted-text"), id="page-content", cls="page-content")
        else:
            transcripts = get_transcripts(company["permno"])
            if transcripts:
                items = []
                for t in transcripts:
                    full_text = t.get("raw_text") or "Full transcript not loaded."
                    items.append(card(
                        H4(f"{t['fiscal_qtr']} — {t['earnings_date']}", cls="transcript-qtr"),
                        Details(
                            Summary("Full Transcript"),
                            P(full_text, cls="transcript-text transcript-full"),
                            open=True
                        ),
                        cls="transcript-card"
                    ))
                body = Div(*items)
            else:
                body = P("No transcripts available for this company.", cls="muted-text")

            content = Div(
                section_title(f"Transcripts — {company['ticker']}"),
                body,
                id="page-content", cls="page-content"
            )

        if 'hx-request' in request.headers:
            return content
        return full_page(content, "transcripts")

    @rt("/api/dcf/calc")
    def post(eps: float = 0.0, growth: float = 0.0, multiple: float = 15.0,
             desired: float = 10.0, growth_spread: float = 40.0,
             multiple_spread: float = 15.0):
        try:
            return _dcf_results(
                eps, growth, multiple, desired, growth_spread, multiple_spread,
            )
        except ValueError as exc:
            body = Div(
                P(f"Invalid assumptions: {exc}", cls="negative"),
                id="dcf-results-area",
            )
            return HTMLResponse(to_xml(body), status_code=422)

    @rt("/dcf")
    def get(request, ticker: str = ""):
        pf = _dcf_prefill(ticker)

        header_bits = [
            section_title("DCF Scenario Ranges"),
            P(
                "All inputs and scenario spreads are user supplied. Results are illustrative, not probabilities or recommendations.",
                cls="muted-text",
            ),
        ]
        if pf["ticker"]:
            sub = pf["name"] or ""
            if pf["price"]:
                sub += f"  ·  Current price {fmt_price(pf['price'])}"
            header_bits.append(P(f"{pf['ticker']} — {sub}", cls="muted-text"))

        content = Div(
            *header_bits,
            Form(
                Input(name="ticker", placeholder="Load a ticker (e.g. MSFT)",
                      value=pf["ticker"], cls="dcf-input"),
                Button("Load", type="submit", cls="dcf-btn"),
                method="get", action="/dcf", cls="dcf-ticker-form",
                style="display:flex;gap:8px;margin-bottom:16px;max-width:420px;"
            ),
            card(
                Form(
                    Div(
                        Label("EPS / FCF (TTM)", fr="dcf-eps"),
                        Input(id="dcf-eps", name="eps", type="number", value=f"{pf['eps']:.2f}", min="0", step="0.01", required=True, cls="dcf-input"),
                        cls="dcf-field"
                    ),
                    Div(
                        Label("Growth Rate %", fr="dcf-growth"),
                        Input(id="dcf-growth", name="growth", type="number", value=f"{pf['growth']:.1f}", min="-99.9", step="0.5", required=True, cls="dcf-input"),
                        cls="dcf-field"
                    ),
                    Div(
                        Label("Exit Multiple", fr="dcf-multiple"),
                        Input(id="dcf-multiple", name="multiple", type="number", value=f"{pf['multiple']:.1f}", min="0", step="1", required=True, cls="dcf-input"),
                        cls="dcf-field"
                    ),
                    Div(
                        Label("Desired Return %", fr="dcf-desired"),
                        Input(id="dcf-desired", name="desired", type="number", value=f"{pf['desired']:.1f}", min="-99.9", step="0.5", required=True, cls="dcf-input"),
                        cls="dcf-field"
                    ),
                    Div(
                        Label("Growth Spread %", fr="dcf-growth-spread"),
                        Input(id="dcf-growth-spread", name="growth_spread", type="number", value=f"{pf['growth_spread']:.1f}", min="0", max="500", step="1", required=True, cls="dcf-input"),
                        cls="dcf-field"
                    ),
                    Div(
                        Label("Multiple Spread %", fr="dcf-multiple-spread"),
                        Input(id="dcf-multiple-spread", name="multiple_spread", type="number", value=f"{pf['multiple_spread']:.1f}", min="0", max="99.9", step="1", required=True, cls="dcf-input"),
                        cls="dcf-field"
                    ),
                    Button("Calculate", type="submit", cls="dcf-btn"),
                    cls="dcf-inputs",
                    hx_post="/api/dcf/calc",
                    hx_target="#dcf-results-area",
                    hx_swap="outerHTML",
                ),
                _dcf_results(
                    pf["eps"], pf["growth"], pf["multiple"], pf["desired"],
                    pf["growth_spread"], pf["multiple_spread"],
                ),
                cls="dcf-card"
            ),
            id="page-content", cls="page-content"
        )
        if 'hx-request' in request.headers:
            return content
        return full_page(content, "dcf")
