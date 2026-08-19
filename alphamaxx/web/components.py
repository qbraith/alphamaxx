"""Formatting helpers and small reusable FastHTML components."""

from __future__ import annotations

from fasthtml.common import Div, H2, P, Span


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _is_nan(v) -> bool:
    """True for NaN or ±inf — values no formatter should render."""
    import math
    return isinstance(v, float) and not math.isfinite(v)

def fmt_b(v) -> str:
    if v is None or _is_nan(v): return "—"
    if abs(v) >= 1_000_000: return f"${v/1_000_000:.2f}T"
    return f"${v/1000:.1f}B" if abs(v) >= 1000 else f"${v:.0f}M"

def fmt_pct(v) -> str:
    if v is None or _is_nan(v): return "—"
    return f"{'+' if v > 0 else ''}{v:.1f}%"

def fmt_price(v) -> str:
    if v is None or _is_nan(v): return "—"
    return f"${v:,.2f}"

def fmt_ratio(v) -> str:
    if v is None or _is_nan(v): return "—"
    return f"{v:.1f}x"

def fmt_num(v, decimals=1) -> str:
    if v is None or _is_nan(v): return "—"
    return f"{v:,.{decimals}f}"

def color_cls(v) -> str:
    if v is None or _is_nan(v): return ""
    return "positive" if v >= 0 else "negative"


# ---------------------------------------------------------------------------
# Reusable UI components
# ---------------------------------------------------------------------------

def card(*children, cls="", **kw):
    return Div(*children, cls=f"card {cls}", **kw)

def metric_row(label: str, value: str, cls_extra: str = ""):
    return Div(
        Span(label, cls="metric-label"),
        Span(value, cls=f"metric-value {cls_extra}"),
        cls="metric-row"
    )

def section_title(text: str):
    return H2(text, cls="section-title")

def stat_tile(label: str, value: str, delta: str = "", delta_cls: str = ""):
    return Div(
        Span(label, cls="stat-tile-label"),
        Span(value, cls="stat-tile-value"),
        Span(delta, cls=f"stat-tile-delta {delta_cls}") if delta else "",
        cls="stat-tile"
    )

def tape_chip(key: str, value: str, value_cls: str = ""):
    return Span(
        Span(key, cls="chip-key"),
        Span(value, cls=value_cls),
        cls="tape-chip"
    )

def empty_state(message: str, hint: str = ""):
    return Div(
        P(message, cls="muted-text"),
        P(hint, cls="muted-text") if hint else "",
        cls="empty-section"
    )
