"""Data Queue page + ingestion-queue API routes."""

from __future__ import annotations

from fasthtml.common import (
    H3, Button, Div, Form, Input, P, Span, Table, Tbody, Td, Th, Thead, Tr,
)

from alphamaxx.data import (
    clear_completed_ingestion_queue,
    clear_failed_ingestion_queue,
    delete_ingestion_queue_item,
    enqueue_missing_ingestion,
    enqueue_ticker,
    get_ingestion_queue,
    get_ingestion_summary,
)
from alphamaxx.web.components import card, metric_row, section_title
from alphamaxx.web.shell import full_page


def _run_active() -> bool:
    """Whether an in-app background queue run is currently in progress.
    Lazy import: ingestion pulls in pandas/yfinance, which the queue page
    itself doesn't need until a run is actually requested."""
    from alphamaxx.services.ingestion import queue_run_active
    return queue_run_active()


def queue_table():
    items = get_ingestion_queue(limit=100)
    if not items:
        return Div(P("No queued downloads yet.", cls="muted-text"), cls="empty-section")
    rows = []
    for item in items:
        requested_ticker = item.get("requested_ticker") or item.get("ticker") or ""
        queued_at = str(item["queued_at"]) if item.get("queued_at") else ""
        rows.append(Tr(
            Td(item["ticker"] or ""),
            Td(item["name"] or ""),
            Td(Span(item["status"], cls=f"queue-status queue-status-{item['status']}")),
            Td(item["reason"] or ""),
            Td(str(item["queued_at"])[:19] if item.get("queued_at") else "—"),
            Td(str(item["fundamentals_count"] or 0), cls="num"),
            Td(str(item["prices_count"] or 0), cls="num"),
            Td(item["error"] or "", cls="muted-text"),
            Td(
                Form(
                    Input(type="hidden", name="ticker", value=requested_ticker),
                    Input(type="hidden", name="queued_at", value=queued_at),
                    Button("Delete", type="submit", cls="queue-delete-btn"),
                    hx_post="/api/ingestion-queue/delete",
                    hx_target="#page-content",
                    hx_swap="outerHTML",
                ),
                cls="queue-action-cell"
            ),
        ))
    return Div(
        Table(
            Thead(Tr(
                Th("Ticker"), Th("Name"), Th("Status"), Th("Reason"),
                Th("Queued"), Th("Fund Rows"), Th("Price Rows"), Th("Error"), Th("")
            )),
            Tbody(*rows),
            cls="data-table"
        ),
        cls="table-wrapper"
    )


def data_queue_content(message: str = "", message_color: str = "var(--muted)"):
    stats = get_ingestion_summary()
    return Div(
        section_title("Data Queue"),
        Div(Span(message, style=f"color:{message_color};") if message else "",
            id="queue-result", cls="queue-result"),
        Div(
            card(
                H3("Queue Downloads", cls="box-title"),
                P("Enter any US-traded ticker. The worker resolves the active WRDS PERMNO before downloading data.",
                  cls="muted-text"),
                Form(
                    Div(
                        Input(name="ticker", placeholder="Ticker, e.g. AAPL",
                              cls="queue-input", autofocus=True),
                        Button("Queue", type="submit", cls="action-btn"),
                        cls="queue-form-row"
                    ),
                    hx_post="/api/ingestion-queue/add-and-refresh",
                    hx_target="#page-content",
                    hx_swap="outerHTML",
                ),
            ),
            card(
                H3("Coverage", cls="box-title"),
                Div(
                    metric_row("Complete", str(stats["complete"]), "positive"),
                    metric_row("Partial", str(stats["partial"])),
                    metric_row("Pending", str(stats["pending"]), "negative" if stats["pending"] else ""),
                ),
                Div(
                    Button("Queue Missing / Partial", cls="action-btn",
                           hx_post="/api/ingestion-queue/queue-missing",
                           hx_target="#page-content",
                           hx_swap="outerHTML"),
                    Button("Clear Completed", cls="action-btn action-btn-secondary",
                           hx_post="/api/ingestion-queue/clear-completed",
                           hx_target="#page-content",
                           hx_swap="outerHTML"),
                    Button("Clear Failed", cls="action-btn action-btn-secondary",
                           hx_post="/api/ingestion-queue/clear-failed",
                           hx_target="#page-content",
                           hx_swap="outerHTML"),
                    cls="queue-actions"
                ),
            ),
            cls="queue-grid"
        ),
        card(
            H3("Run Queued Downloads", cls="box-title"),
            P("Run the ingestion worker in the background (the app holds the "
              "database, so the CLI can only run while the app is stopped).",
              cls="muted-text"),
            Button("Run in progress…" if _run_active() else "Run Queue Now",
                   cls="action-btn", disabled=_run_active() or None,
                   hx_post="/api/ingestion-queue/run",
                   hx_target="#page-content",
                   hx_swap="outerHTML"),
            P("Offline alternative: python3 ingestion.py --queued", cls="code-hint"),
        ),
        section_title("Queued Companies"),
        queue_table(),
        id="page-content", cls="page-content"
    )


def register(app, rt):

    @rt("/data-queue")
    def get(request):
        content = data_queue_content()
        if 'hx-request' in request.headers:
            return content
        return full_page(content, "data-queue")

    @rt("/api/ingestion-queue/add")
    def post(ticker: str = ""):
        ok, msg = enqueue_ticker(ticker, "manual")
        color = "var(--teal)" if ok else "var(--orange)"
        return Span(msg, style=f"color:{color};")

    @rt("/api/ingestion-queue/add-and-refresh")
    def post(ticker: str = ""):
        ok, msg = enqueue_ticker(ticker, "manual")
        color = "var(--teal)" if ok else "var(--orange)"
        return data_queue_content(msg, color)

    @rt("/api/ingestion-queue/queue-missing")
    def post(request):
        enqueue_missing_ingestion()
        return data_queue_content()

    @rt("/api/ingestion-queue/clear-completed")
    def post(request):
        clear_completed_ingestion_queue()
        return data_queue_content()

    @rt("/api/ingestion-queue/clear-failed")
    def post(request):
        clear_failed_ingestion_queue()
        return data_queue_content()

    @rt("/api/ingestion-queue/run")
    def post(request):
        from alphamaxx.services.ingestion import run_queue_in_background
        if run_queue_in_background():
            return data_queue_content(
                "Queue run started in the background — statuses update as items finish.",
                "var(--teal)")
        return data_queue_content("A queue run is already in progress.", "var(--orange)")

    @rt("/api/ingestion-queue/delete")
    def post(ticker: str = "", queued_at: str = ""):
        deleted = delete_ingestion_queue_item(ticker, queued_at)
        if deleted:
            return data_queue_content(f"Removed {ticker.upper()} from the data queue.", "var(--teal)")
        return data_queue_content("That queue item could not be found.", "var(--orange)")
