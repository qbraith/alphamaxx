"""Watchlist and Portfolio pages + their add/remove API routes."""

from __future__ import annotations

from fasthtml.common import (
    H3, A, Button, Div, Form, Input, Span, Table, Tbody, Td, Th, Thead, Tr,
)

from alphamaxx.data import (
    get_company_by_ticker,
    get_correlation_matrix,
    get_portfolio_summary,
    get_watchlist_summary,
    set_portfolio,
    set_watchlist,
)
from alphamaxx.services.yf_client import fetch_yf_valuations
from alphamaxx.web.components import (
    card, color_cls, fmt_num, fmt_pct, fmt_price, fmt_ratio, section_title,
)
from alphamaxx.web.shell import full_page

_FUND_NAME_MARKERS = (
    " ETF", "E T F", "EXCHANGE TRADED", "INDEX FUND", "INDEX FUNDS",
)


def _looks_like_fund(company: dict) -> bool:
    name = f" {str(company.get('name') or '').upper()} "
    return any(marker in name for marker in _FUND_NAME_MARKERS)


def _add_modal(modal_id: str, input_id: str, result_id: str, title: str,
               post_url: str, refresh_url: str):
    return Div(
        Div(
            Div(
                H3(title, style="margin:0;font-size:16px;color:var(--text);"),
                Button("✕", cls="modal-close",
                       onclick=f"document.getElementById('{modal_id}').style.display='none';"),
                style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;"
            ),
            Form(
                Div(
                    Input(id=input_id, name="ticker", placeholder="Ticker (e.g. AAPL)",
                          style="width:100%;padding:10px 14px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:14px;outline:none;"),
                    style="margin-bottom:12px;"
                ),
                Div(id=result_id, style="margin-bottom:12px;font-size:13px;"),
                Button(title, type="submit", cls="action-btn", style="width:100%;"),
                hx_post=post_url,
                hx_target=f"#{result_id}",
                hx_swap="innerHTML",
                hx_on__after_request=f"if(document.getElementById('{result_id}').innerText.includes('Added')){{setTimeout(function(){{document.getElementById('{modal_id}').style.display='none';htmx.ajax('GET','{refresh_url}',{{target:'#page-content',swap:'outerHTML'}});}},800);}}"
            ),
            cls="modal-content", style="max-width:360px;"
        ),
        id=modal_id, cls="chart-modal", style="display:none;",
        onclick="if(event.target===this)this.style.display='none';"
    )


def correlation_section(portfolio: list[dict]):
    """Correlation heatmap rendered as a colored HTML table (non-ETF tickers)."""
    non_etf = [company for company in portfolio if not _looks_like_fund(company)]
    corr_data = get_correlation_matrix(
        [c["permno"] for c in non_etf],
        [c["ticker"] for c in non_etf]
    )
    if not corr_data:
        return ""
    ct = corr_data["tickers"]
    cm = corr_data["matrix"]

    def _heatmap_color(v, i, j):
        if i == j or v is None:
            return "background:rgba(255,255,255,0.05);color:var(--muted)"
        # Interpolate: -1=blue(59,130,246) → 0=dark(15,18,25) → +1=red(244,63,94)
        if v >= 0:
            r = int(15 + v * (244 - 15))
            g = int(18 + v * (63 - 18))
            b = int(25 + v * (94 - 25))
        else:
            t = -v
            r = int(15 + t * (59 - 15))
            g = int(18 + t * (130 - 18))
            b = int(25 + t * (246 - 25))
        return f"background:rgb({r},{g},{b});color:rgba(255,255,255,0.85)"

    corr_rows = []
    for i, t in enumerate(ct):
        corr_rows.append(Tr(
            Th(t),
            *[Td("—" if cm[i][j] is None else f"{cm[i][j]:.2f}",
                 style=_heatmap_color(cm[i][j], i, j))
              for j in range(len(ct))]
        ))
    return Div(
        section_title("Correlation Matrix"),
        Div(
            Table(
                Thead(Tr(Th(""), *[Th(t) for t in ct])),
                Tbody(*corr_rows),
                cls="data-table covariance-table"
            ),
            cls="table-wrapper", style="overflow-x:auto;"
        ),
        cls="covariance-section"
    )


def register(app, rt):

    @rt("/watchlist")
    def get(request):
        watchlist = get_watchlist_summary()
        yf_vals = fetch_yf_valuations([c["ticker"] for c in watchlist])
        rows = []
        for c in watchlist:
            v = yf_vals.get(c["ticker"], {})
            pe = v.get("pe")
            peg = v.get("peg")
            forward_pe = v.get("forward_pe")
            rows.append(Tr(
                Td(Span(c["ticker"], cls="ticker-link")),
                Td(c["name"] or ""),
                Td(fmt_price(c.get("price_current")), cls="num"),
                Td(fmt_ratio(pe), cls="num"),
                Td(fmt_ratio(forward_pe), cls="num"),
                Td(fmt_num(peg, 2), cls="num"),
                Td(Span(fmt_pct(c.get("pct_from_50")), cls=color_cls(c.get("pct_from_50"))), cls="num"),
                Td(fmt_num(c.get("rsi_14"), 0), cls="num"),
                Td(Button("↓", cls="queue-icon-btn", title=f"Queue {c['ticker']} for data download",
                          hx_post=f"/api/ingestion-queue/add?ticker={c['ticker']}",
                          hx_target="#queue-toast",
                          hx_swap="innerHTML",
                          onclick="event.stopPropagation();"),
                   cls="num"),
                cls="watchlist-row",
                title=f"Double-click to remove {c['ticker']} from watchlist",
                hx_post=f"/api/watchlist/remove?permno={c['permno']}",
                hx_trigger="dblclick",
                hx_target="closest tr",
                hx_swap="delete",
            ))

        content = Div(
            _add_modal("wl-modal", "wl-ticker-input", "wl-add-result",
                       "Add to Watchlist", "/api/watchlist/add", "/watchlist"),
            Div(id="queue-toast", cls="queue-toast"),
            Div(
                section_title("Watchlist"),
                Button("✚", cls="wl-add-btn", title="Add to Watchlist",
                       aria_label="Add to Watchlist",
                       onclick="document.getElementById('wl-modal').style.display='flex';document.getElementById('wl-ticker-input').focus();document.getElementById('wl-add-result').innerHTML='';"),
                style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;"
            ),
            Div(
                Table(
                    Thead(Tr(Th("Ticker"), Th("Name"), Th("Price"), Th("P/E"), Th("Fwd P/E"), Th("PEG"), Th("% 50d SMA"), Th("RSI 14W"), Th("Queue"))),
                    Tbody(*rows),
                    cls="data-table"
                ),
                cls="table-wrapper"
            ),
            id="page-content", cls="page-content"
        )
        if 'hx-request' in request.headers:
            return content
        return full_page(content, "watchlist")

    @rt("/portfolio")
    def get(request):
        portfolio = get_portfolio_summary()
        yf_vals = fetch_yf_valuations([c["ticker"] for c in portfolio])
        rows = []
        for c in portfolio:
            v = yf_vals.get(c["ticker"], {})
            pe = v.get("pe")
            peg = v.get("peg")
            forward_pe = v.get("forward_pe")
            rsi = c.get("rsi_14")
            rows.append(Tr(
                Td(A(c["ticker"], href=f"/stock/{c['ticker']}",
                     hx_get=f"/stock/{c['ticker']}", hx_target="#page-content",
                     hx_swap="outerHTML", hx_push_url="true", cls="ticker-link")),
                Td(c["name"] or ""),
                Td(fmt_price(c.get("price_current")), cls="num"),
                Td(Span(fmt_pct(c.get("pct_from_50")), cls=color_cls(c.get("pct_from_50"))), cls="num"),
                Td(fmt_ratio(pe), cls="num"),
                Td(fmt_ratio(forward_pe), cls="num"),
                Td(fmt_num(peg, 2), cls="num"),
                Td(fmt_num(rsi, 1), cls="num"),
                cls="portfolio-row",
                title=f"Double-click to remove {c['ticker']} from portfolio",
                hx_post=f"/api/portfolio/remove?permno={c['permno']}",
                hx_trigger="dblclick",
                hx_target="closest tr",
                hx_swap="delete",
            ))

        content = Div(
            _add_modal("portfolio-modal", "portfolio-ticker-input", "portfolio-add-result",
                       "Add to Portfolio", "/api/portfolio/add", "/portfolio"),
            Div(
                section_title("Portfolio Analysis"),
                Button("✚", cls="wl-add-btn", title="Add to Portfolio",
                       aria_label="Add to Portfolio",
                       onclick="document.getElementById('portfolio-modal').style.display='flex';document.getElementById('portfolio-ticker-input').focus();document.getElementById('portfolio-add-result').innerHTML='';"),
                style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;"
            ),
            Div(
                Table(
                    Thead(Tr(Th("Ticker"), Th("Name"), Th("Price"), Th("% 50d SMA"), Th("P/E"), Th("Fwd P/E"), Th("PEG"), Th("RSI 14W"))),
                    Tbody(*rows),
                    cls="data-table"
                ),
                cls="table-wrapper"
            ),
            correlation_section(portfolio),
            id="page-content", cls="page-content"
        )
        if 'hx-request' in request.headers:
            return content
        return full_page(content, "portfolio")

    @rt("/api/watchlist/add")
    def post(ticker: str = ""):
        ticker = ticker.strip().upper()
        if not ticker:
            return Span("Please enter a ticker.", style="color:var(--red);")
        company = get_company_by_ticker(ticker)
        if not company:
            return Span(f"{ticker} not found in database.", style="color:var(--red);")
        set_watchlist(company["permno"], True)
        return Span(f"Added {ticker} to watchlist.", style="color:var(--teal);")

    @rt("/api/watchlist/remove")
    def post(permno: int = 0):
        if not permno:
            return ""
        set_watchlist(permno, False)
        return ""

    @rt("/api/portfolio/add")
    def post(ticker: str = ""):
        ticker = ticker.strip().upper()
        if not ticker:
            return Span("Please enter a ticker.", style="color:var(--red);")
        company = get_company_by_ticker(ticker)
        if not company:
            return Span(f"{ticker} not found in database.", style="color:var(--red);")
        set_portfolio(company["permno"], True)
        # Portfolio membership should not wait until the next market-hours
        # scheduler gate for a usable price. This Yahoo-only refresh works for
        # ETFs and other local identities even when WRDS has no fundamentals.
        from alphamaxx.services.price_updater import start_ticker_price_refresh
        start_ticker_price_refresh(company["permno"], ticker)
        return Span(
            f"Added {ticker} to portfolio; price refresh started.",
            style="color:var(--teal);",
        )

    @rt("/api/portfolio/remove")
    def post(permno: int = 0):
        if not permno:
            return ""
        set_portfolio(permno, False)
        return ""
