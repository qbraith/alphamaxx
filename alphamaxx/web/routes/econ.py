"""Economic calendar — US macro events with 'why it matters' notes.

Two views (list / month grid), each event expandable to its current
description (static seed or AI rewrite) with a manual 'Update now' button.
Macro events have no ticker, so there are no stock links here.
"""

from __future__ import annotations

import calendar as _cal
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fasthtml.common import (
    A, Button, Details, Div, NotStr, P, Span, Summary,
    Table, Tbody, Td, Th, Thead, Tr,
)
from alphamaxx.config import settings
from alphamaxx.services.llm_provider import is_configured

log = logging.getLogger(__name__)

from alphamaxx.data import (
    current_econ_descriptions_for,
    econ_events_between,
)
from alphamaxx.web.components import section_title
from alphamaxx.web.shell import full_page

ET = ZoneInfo("America/New_York")

_IMPORTANCE_FLAG = {"high": "🔴", "med": "🟠", "low": "🟡"}
_TIER_BADGE = {
    "static": "📄 static", "m1": "🤖 ai · 1mo", "w2": "🤖 ai · 2wk",
    "w1": "🤖 ai · 1wk", "manual": "🤖 ai · updated",
}


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _et(dt: datetime) -> datetime:
    """Coerce a stored event_time to America/New_York for display."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET)


def _md(body: str) -> NotStr:
    """Minimal, safe markdown → HTML: escapes first, then renders **bold**,
    bullet lists, and paragraphs. Sufficient for our seed / AI note bodies."""
    out: list[str] = []
    in_list = False
    for raw in (body or "").split("\n"):
        line = html.escape(raw.strip())
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line:
            out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    return NotStr("".join(out))


def _flag(importance: str) -> Span:
    imp = (importance or "low").lower()
    return Span(_IMPORTANCE_FLAG.get(imp, "🟡"), cls=f"econ-flag econ-flag-{imp}",
                title=imp)


def _desc_block(event_id: str, descriptions: dict | None = None) -> Div:
    """The current-description panel for one event (the swap target of the
    'Update now' POST). Pass a pre-loaded {event_id: desc} map to avoid N+1."""
    if descriptions is None:
        descriptions = current_econ_descriptions_for([event_id])
    desc = descriptions.get(event_id)
    if desc:
        badge = _TIER_BADGE.get(desc["tier"], desc["tier"])
        inner = [Span(badge, cls="econ-tier-badge"), Div(_md(desc["body"]), cls="econ-md")]
    else:
        inner = [P("No description yet.", cls="muted-text")]
    return Div(
        *inner,
        Button("Update now", hx_post=f"/econ-calendar/update/{event_id}",
               hx_target=f"#desc-{event_id}", hx_swap="outerHTML",
               cls="econ-update-btn"),
        id=f"desc-{event_id}", cls="econ-desc",
    )


def _event_detail(ev: dict, descriptions: dict) -> Details:
    """Expandable detail row: time/forecast/previous + the description panel."""
    when = _et(ev["event_time"])
    meta_bits = [Span(when.strftime("%I:%M %p ET"), cls="num")]
    if ev.get("forecast"):
        meta_bits.append(Span(f"Forecast {ev['forecast']}", cls="num muted-text"))
    if ev.get("previous"):
        meta_bits.append(Span(f"Previous {ev['previous']}", cls="num muted-text"))
    return Details(
        Summary(_flag(ev["importance"]), Span(ev["title"], cls="econ-event-title")),
        Div(*[Span(b, cls="econ-meta-item") for b in meta_bits], cls="econ-meta"),
        _desc_block(ev["event_id"], descriptions),
        cls="econ-detail",
    )


# --------------------------------------------------------------------------- #
# List view                                                                    #
# --------------------------------------------------------------------------- #
def _day_list() -> Div:
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=14)
    events = econ_events_between(now, end)
    descriptions = current_econ_descriptions_for([e["event_id"] for e in events])
    today = datetime.now(ET).date()

    # Group by local (ET) day.
    by_day: dict = {}
    for ev in events:
        by_day.setdefault(_et(ev["event_time"]).date(), []).append(ev)

    sections = []
    if not by_day:
        sections.append(P("No macro events in the next two weeks. "
                          "Run the ingest CLI to populate the calendar.",
                          cls="muted-text", style="padding:12px 0;"))
    for day in sorted(by_day):
        is_today = day == today
        header_cls = "earnings-day-header earnings-today" if is_today else "earnings-day-header"
        today_tag = Span(" — Today", cls="today-tag") if is_today else ""
        rows = []
        for ev in by_day[day]:
            when = _et(ev["event_time"])
            rows.append(Tr(
                Td(Span(when.strftime("%I:%M %p"), cls="time-badge")),
                Td(_flag(ev["importance"])),
                Td(_event_detail(ev, descriptions)),
                Td(ev.get("forecast") or "—", cls="num"),
                Td(ev.get("previous") or "—", cls="num"),
            ))
        sections.append(Div(
            Span(day.strftime("%A, %B %-d"), today_tag, cls=header_cls),
            Div(Table(
                Thead(Tr(Th("Time"), Th("Imp."), Th("Event"),
                         Th("Forecast"), Th("Previous"))),
                Tbody(*rows), cls="data-table econ-table",
            ), cls="table-wrapper"),
            cls="earnings-day",
        ))
    return Div(*sections, cls="econ-list")


# --------------------------------------------------------------------------- #
# Grid view                                                                    #
# --------------------------------------------------------------------------- #
def _calendar_grid() -> Div:
    today = datetime.now(ET).date()
    first = today.replace(day=1)
    last_day = _cal.monthrange(today.year, today.month)[1]
    last = today.replace(day=last_day)

    # Pull the whole month (UTC bounds spanning the local month generously).
    start = datetime(first.year, first.month, first.day, tzinfo=ET).astimezone(timezone.utc)
    end = (datetime(last.year, last.month, last.day, tzinfo=ET)
           + timedelta(days=1)).astimezone(timezone.utc)
    events = econ_events_between(start, end)
    by_day: dict = {}
    for ev in events:
        by_day.setdefault(_et(ev["event_time"]).date(), []).append(ev)

    # Day-of-week headers (Mon-first to match calendar.monthcalendar).
    headers = [Div(d, cls="econ-grid-head")
               for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")]
    cells = []
    for week in _cal.monthcalendar(today.year, today.month):
        for dom in week:
            if dom == 0:
                cells.append(Div(cls="econ-cell econ-cell-empty"))
                continue
            day = today.replace(day=dom)
            cell_cls = "econ-cell econ-cell-today" if day == today else "econ-cell"
            day_events = sorted(by_day.get(day, []),
                                key=lambda e: e["event_time"])
            event_rows = [
                Div(_flag(ev["importance"]),
                    Span(ev["title"], cls="econ-cell-title"),
                    cls=f"econ-cell-event econ-imp-{(ev['importance'] or 'low').lower()}",
                    title=ev["title"])
                for ev in day_events
            ]
            cells.append(Div(
                Span(str(dom), cls="econ-cell-date"),
                *event_rows,
                cls=cell_cls,
            ))
    return Div(
        P(today.strftime("%B %Y"), cls="econ-grid-month"),
        Div(*headers, *cells, cls="econ-grid"),
        cls="econ-grid-wrap",
    )


# --------------------------------------------------------------------------- #
# View toggle                                                                  #
# --------------------------------------------------------------------------- #
def _view_toggle(view: str) -> Div:
    def btn(key, label):
        cls = "econ-toggle-btn active" if view == key else "econ-toggle-btn"
        return A(label, href=f"/econ-calendar?view={key}",
                 hx_get=f"/econ-calendar?view={key}", hx_target="#page-content",
                 hx_swap="outerHTML", hx_push_url="true", cls=cls)
    return Div(btn("list", "List"), btn("grid", "Calendar"), cls="econ-toggle")


def _has_ai_key() -> bool:
    return is_configured()


def _controls(view: str) -> Div:
    """Header row: view toggle + the in-app Sync button."""
    ai_note = (Span(f"AI: {settings.AI_ENGINE}", cls="econ-ai-on")
               if _has_ai_key() else Span("AI off · static only", cls="econ-ai-off"))
    sync_btn = Button(
        "⟳ Sync", hx_post=f"/econ-calendar/sync?view={view}",
        hx_target="#page-content", hx_swap="outerHTML",
        cls="econ-sync-btn", title="Fetch new events now; AI notes generate in the background",
    )
    return Div(_view_toggle(view), Div(ai_note, sync_btn, cls="econ-controls-right"),
               cls="econ-controls")


def _page(view: str, status: str | None = None) -> Div:
    """The #page-content fragment for the calendar (shared by GET and sync POST)."""
    body = _calendar_grid() if view == "grid" else _day_list()
    children = [
        section_title("Economic Calendar — US Macro Events"),
        _controls(view),
    ]
    if status:
        children.append(P(status, cls="econ-sync-status"))
    children.append(body)
    return Div(*children, id="page-content", cls="page-content")


# --------------------------------------------------------------------------- #
# Routes                                                                       #
# --------------------------------------------------------------------------- #
def register(app, rt):

    @rt("/econ-calendar")
    def get(request):
        view = request.query_params.get("view", "list")
        content = _page(view)
        if 'hx-request' in request.headers:
            return content
        return full_page(content, "econ-calendar")

    @rt("/econ-calendar/sync")
    def post(request):
        view = request.query_params.get("view", "list")
        from alphamaxx.services.econ_calendar import sync
        try:
            summary = sync()
        except Exception:  # network/feed failure must not break the page
            return _page(view, status="Sync failed — feed unreachable. Try again shortly.")
        tail = " · generating AI notes…" if _has_ai_key() else ""
        status = f"Synced {summary['events']} events ({summary['seeded']} new){tail}"
        return _page(view, status=status)

    @rt("/econ-calendar/update/{event_id}")
    def post(event_id: str):
        from alphamaxx.services.econ_calendar import update_event
        try:
            update_event(event_id)
        except Exception:  # never break the page on a failed AI call
            log.exception("Update-now AI rewrite failed for event %s", event_id)
        return _desc_block(event_id)
