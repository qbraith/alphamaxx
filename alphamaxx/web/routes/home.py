"""Home dashboard: quick stats + quick-access ticker cards."""

from __future__ import annotations

from fasthtml.common import H1, A, Br, Div, P, Span

from alphamaxx.data import (
    count_pending_queue,
    get_ingestion_summary,
    get_latest_price_date,
    get_watchlist_summary,
)
from alphamaxx.web.components import color_cls, fmt_pct, fmt_price, section_title
from alphamaxx.web.shell import full_page, logo_color


def register(app, rt):

    @rt("/api/status-bar")
    def get():
        stats = get_ingestion_summary()
        pending = count_pending_queue()
        last_price = get_latest_price_date() or "—"
        return (
            Div(Span("DB", cls="status-key"),
                Span(f"{stats['total']} companies", cls="status-val"),
                cls="status-item"),
            Div(Span("COMPLETE", cls="status-key"),
                Span(str(stats["complete"]), cls="status-val positive"),
                cls="status-item"),
            Div(Span("QUEUE", cls="status-key"),
                Span(f"{pending} pending", cls=f"status-val{' warn' if pending else ''}"),
                cls="status-item"),
            Div(Span("PRICES", cls="status-key"),
                Span(last_price, cls="status-val"),
                cls="status-item"),
        )

    @rt("/")
    def get(request):
        stats = get_ingestion_summary()
        companies = get_watchlist_summary()

        ticker_cards = []
        for c in companies:
            pct = c.get("pct_from_200")
            pct_str = fmt_pct(pct) if pct is not None else ""
            pct_cls = color_cls(pct)
            ticker_cards.append(
                A(
                    Div(
                        Div(c["ticker"][0], cls="logo-circle",
                            style=f"background:{logo_color(c['ticker'])};width:32px;height:32px;font-size:14px;"),
                        Div(
                            Span(c["ticker"], style="font-weight:700;color:var(--amethyst);font-size:14px;"),
                            Br(),
                            Span(c["name"] or "", style="font-size:11px;color:var(--muted);"),
                            style="flex:1;min-width:0;overflow:hidden;"
                        ),
                        Div(
                            Span(fmt_price(c.get("price_current")), style="font-size:13px;font-weight:600;"),
                            Br(),
                            Span(pct_str, cls=pct_cls, style="font-size:11px;"),
                        ),
                        style="display:flex;align-items:center;gap:10px;"
                    ),
                    cls="card ticker-card",
                    href=f"/stock/{c['ticker']}",
                    hx_get=f"/stock/{c['ticker']}",
                    hx_target="#page-content",
                    hx_swap="outerHTML",
                    hx_push_url="true",
                )
            )

        content = Div(
            Div(
                H1("ALPHAMAXX TERMINAL", cls="hero-title"),
                P("Local-first market intelligence · WRDS fundamentals · DuckDB",
                  cls="muted-text", style="font-size:11px;margin-top:4px;letter-spacing:0.04em;"),
                cls="welcome-hero"
            ),
            Div(
                Div(Span(str(stats["total"]), cls="stat-num"), Span("Companies Tracked", cls="stat-label"), cls="stat-box"),
                Div(Span(str(stats["complete"]), cls="stat-num positive"), Span("Fully Ingested", cls="stat-label"), cls="stat-box"),
                Div(Span(str(stats["partial"]), cls="stat-num"), Span("Partial Data", cls="stat-label"), cls="stat-box"),
                Div(Span(str(stats["pending"]), cls="stat-num muted-text"), Span("Pending", cls="stat-label"), cls="stat-box"),
                cls="quick-stats"
            ),
            Div(
                A("Browse Watchlist", href="/watchlist", hx_get="/watchlist", hx_target="#page-content",
                  hx_swap="outerHTML", hx_push_url="true", cls="action-btn"),
                A("Data Queue", href="/data-queue", hx_get="/data-queue", hx_target="#page-content",
                  hx_swap="outerHTML", hx_push_url="true", cls="action-btn action-btn-secondary"),
                A("DCF Calculator", href="/dcf", hx_get="/dcf", hx_target="#page-content",
                  hx_swap="outerHTML", hx_push_url="true", cls="action-btn action-btn-secondary"),
                cls="welcome-actions"
            ),
            section_title("Quick Access — Watchlist"),
            Div(*ticker_cards, cls="ticker-cards-grid") if ticker_cards else
                Div(P("Your watchlist is empty.", cls="muted-text"),
                    P("Queue a ticker from Data Queue, run ingestion, then add the resolved company from Watch.", cls="muted-text"),
                    cls="empty-section"),
            id="page-content", cls="page-content welcome-page"
        )
        if 'hx-request' in request.headers:
            return content
        return full_page(content, "home")
