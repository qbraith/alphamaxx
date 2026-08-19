"""Page chrome: command bar (top), nav rail (left), status bar (bottom).

Every route returns its content fragment directly for HTMX requests and
wraps it in full_page() for direct browser loads.
"""

from __future__ import annotations

from fasthtml.common import A, Aside, Div, Footer, Form, Input, Nav, NotStr, Span, Title

from alphamaxx.services.yf_client import get_indices


def _svg(paths: str) -> NotStr:
    """14px stroke icon that inherits the rail link's currentColor."""
    return NotStr(
        '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round">{paths}</svg>'
    )


# Star glyph path, shared with the star_toggle component in components.py
STAR_PATH = '<path d="M12 3l2.7 5.8 6.3.8-4.6 4.3 1.2 6.1L12 17l-5.6 3 1.2-6.1L3 9.6l6.3-.8z"/>'

_ICONS = {
    "home":  '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>',
    "star":  STAR_PATH,
    "grid":  '<rect x="3" y="3" width="8" height="8"/><rect x="13" y="3" width="8" height="8"/>'
             '<rect x="3" y="13" width="8" height="8"/><rect x="13" y="13" width="8" height="8"/>',
    "cal":   '<rect x="3" y="5" width="18" height="16"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "econ":  '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
    "fx":    '<path d="M4 20c4 0 4-16 8-16M6 9h7"/><path d="M14 13l6 7M20 13l-6 7"/>',
    "doc":   '<path d="M6 2h9l4 4v16H6z"/><path d="M9 11h7M9 15h7"/>',
    "down":  '<path d="M12 3v12"/><path d="M6 11l6 6 6-6"/><path d="M4 21h16"/>',
}

# (key, icon, label, href) — key matches full_page(active=…)
RAIL_LINKS = [
    ("home",              "home",  "Home",  "/"),
    ("watchlist",         "star",  "Watch", "/watchlist"),
    ("portfolio",         "grid",  "Port",  "/portfolio"),
    ("earnings-calendar", "cal",   "Earn",  "/earnings-calendar"),
    ("econ-calendar",     "econ",  "Econ",  "/econ-calendar"),
    ("dcf",               "fx",    "DCF",   "/dcf"),
    ("transcripts",       "doc",   "Docs",  "/transcripts"),
    ("data-queue",        "down",  "Queue", "/data-queue"),
]


def logo_color(ticker: str) -> str:
    """Generate a deterministic chip color from a ticker string."""
    colors = ["#8B5CF6", "#14B8A6", "#F97316", "#06B6D4", "#F43F5E",
              "#3B82F6", "#22D3EE", "#A855F7", "#EC4899", "#10B981"]
    return colors[sum(ord(c) for c in ticker) % len(colors)]


def command_bar():
    index_badges = []
    for idx in get_indices():
        cls = "positive" if idx["pct"] >= 0 else "negative"
        index_badges.append(
            Span(
                Span(idx["name"], cls="index-name"),
                Span(f"{'+' if idx['pct'] >= 0 else ''}{idx['pct']:.2f}%", cls=f"index-val {cls}"),
                cls="index-badge"
            )
        )

    return Nav(
        Div(A("ALPHAMAXX", href="/", cls="brand"), cls="nav-brand"),
        Form(
            Span(">", cls="search-prompt"),
            Input(id="ticker-search", name="q", placeholder="ticker or company…",
                  cls="search-input", hx_get="/api/search",
                  hx_trigger="keyup changed delay:300ms",
                  hx_target="#search-results", autocomplete="off"),
            Span("/", cls="search-kbd"),
            Div(id="search-results", cls="search-results"),
            cls="search-container", action="/api/search/open", method="get",
            hx_get="/api/search/open", hx_swap="none"
        ),
        Div(*index_badges, cls="nav-indices"),
        cls="command-bar"
    )


def nav_rail(active: str = "home"):
    items = []
    for key, icon, label, href in RAIL_LINKS:
        cls = "rail-link active" if key == active else "rail-link"
        items.append(
            A(Span(_svg(_ICONS[icon]), cls="rail-icon"), Span(label, cls="rail-label"),
              href=href, hx_get=href, hx_target="#page-content",
              hx_swap="outerHTML", hx_push_url="true", cls=cls)
        )
    return Aside(*items, cls="nav-rail")


def status_bar():
    return Footer(
        Div(hx_get="/api/status-bar", hx_trigger="load, every 60s",
            hx_swap="innerHTML", id="status-bar-content",
            style="display:flex;gap:18px;align-items:baseline;flex:1;min-width:0;"),
        Span("DUCKDB · LOCAL", cls="status-live"),
        cls="status-bar", id="status-bar",
    )


def full_page(content, active_sidebar="home"):
    """Wrap content in the full app shell: command bar + rail + status bar."""
    return Title("AlphaMaxx"), Div(
        command_bar(),
        Div(nav_rail(active_sidebar),
            Div(content, cls="content-area"),
            cls="app-layout"),
        status_bar(),
        cls="app-shell"
    )
