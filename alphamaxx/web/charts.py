"""The single Chart.js builder module — grid panes and detail/modal views.

Every builder returns a FastHTML fragment containing a
<canvas data-chart-config="…"> that static/app.js initialises on
DOMContentLoaded / htmx:afterSettle. mode="grid" renders compact panes;
mode="detail" renders the tall modal view with legend and dense ticks.
"""

from __future__ import annotations

import json

from fasthtml.common import Canvas, Div, P

from alphamaxx.config import settings

# Obsidian & Amethyst chart palette (kept in sync with style.css tokens)
GRID_COLOR = "#1E2733"   # --line
TEXT_COLOR = "#E2E8F0"   # --text
MUTED = "#64748B"        # --dim
AMETHYST = "#8B5CF6"     # --accent
TEAL = "#14B8A6"         # --up
RED = "#F43F5E"          # --down
CYAN = "#22D3EE"
ORANGE = "#F97316"       # --warn
GOLD = "#F59E0B"
SLATE = "#94A3B8"
BLUE = "#60A5FA"

# Aliases used by shared builders
UP = TEAL
DOWN = RED
INFO = CYAN
VIOLET = AMETHYST
WARN = ORANGE
AMBER = AMETHYST  # accent alias retained from the Tape iteration

SERIES_COLORS = [AMETHYST, TEAL, ORANGE, RED, CYAN, BLUE, "#A78BFA"]

METRIC_META = {
    "revenue": {"title": "Revenue", "label": "Revenue TTM ($M)", "color": TEAL,
                "margin_label": "Gross Margin %"},
    "ebitda": {"title": "EBITDA", "label": "EBITDA TTM ($M)", "color": AMETHYST,
               "margin_label": "EBITDA Margin %"},
    "net_income": {"title": "Net Income", "label": "Net Income TTM ($M)", "color": CYAN,
                   "margin_label": "Net Margin %"},
    "fcf": {"title": "Free Cash Flow", "label": "FCF TTM ($M)", "color": TEAL,
            "margin_label": "FCF Margin %"},
    "eps": {"title": "EPS", "label": "EPS TTM ($)", "color": CYAN},
    "sbc": {"title": "Stock-Based Comp", "label": "SBC TTM ($M)", "color": ORANGE},
    "capex": {"title": "CAPEX", "label": "CAPEX TTM ($M)", "color": GOLD},
    "shares": {"title": "Shares Outstanding", "label": "Shares Outstanding (M)", "color": SLATE},
    "cash_debt": {"title": "Cash & Debt", "label": "Cash & Debt ($M)", "color": TEAL},
    "segments": {"title": "Revenue By Segment", "label": "Revenue ($M)", "color": AMETHYST},
    "price": {"title": "Price", "label": "Price ($)", "color": TEAL},
    "dividends": {"title": "Dividends", "label": "Dividend / Share ($)", "color": AMETHYST},
}


def metric_title(metric: str) -> str:
    return METRIC_META.get(metric, {}).get("title", metric.replace("_", " ").title())


def empty_chart(message: str = "No chart data available."):
    return Div(P(message, cls="muted-text"), cls="chart-empty")


# ---------------------------------------------------------------------------
# Shared config pieces
# ---------------------------------------------------------------------------

def _max_ticks(mode: str) -> int:
    return settings.DETAIL_MAX_TICKS if mode == "detail" else settings.GRID_MAX_TICKS


def _x_axis(mode: str) -> dict:
    return {
        "ticks": {"color": MUTED, "font": {"size": 9}, "maxTicksLimit": _max_ticks(mode),
                  "autoSkip": True, "maxRotation": 0},
        "grid": {"color": GRID_COLOR},
    }


def _y_axis(mode: str, title: str = "") -> dict:
    axis = {"ticks": {"color": TEXT_COLOR}, "grid": {"color": GRID_COLOR}}
    if mode == "detail" and title:
        axis["title"] = {"display": True, "text": title, "color": MUTED}
    return axis


def _legend(mode: str, force: bool | None = None) -> dict:
    show = force if force is not None else (mode == "detail")
    return {"display": show, "labels": {"color": TEXT_COLOR, "font": {"size": 10}}}


def _tooltip() -> dict:
    return {
        "backgroundColor": "rgba(11,14,20,0.95)",
        "borderColor": GRID_COLOR, "borderWidth": 1,
        "titleColor": TEXT_COLOR, "bodyColor": SLATE, "padding": 10,
    }


def _options(mode: str, y_title: str = "", legend: bool | None = None,
             stacked: bool = False) -> dict:
    x = _x_axis(mode)
    y = _y_axis(mode, y_title)
    if stacked:
        x["stacked"] = True
        y["stacked"] = True
    return {
        "responsive": True, "maintainAspectRatio": False, "animation": False,
        "interaction": {"mode": "index", "intersect": False},
        "plugins": {"legend": _legend(mode, legend), "tooltip": _tooltip()},
        "scales": {"x": x, "y": y},
    }


def _chart_component(chart_id: str, config: dict, mode: str = "grid"):
    """Canvas payload initialized by the HTMX-aware bootstrap in app.js."""
    style = f"height:{settings.CHART_DETAIL_HEIGHT}px;" if mode == "detail" else ""
    return Div(
        Canvas(id=chart_id, cls="chart-canvas", data_chart_config=json.dumps(config)),
        cls=f"chart-inner chart-inner-{mode}",
        style=style,
    )


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def bar_chart(chart_id, labels, values, label, color, mode="grid"):
    config = {
        "type": "bar",
        "data": {"labels": labels, "datasets": [
            {"label": label, "data": values, "backgroundColor": color + "CC",
             "borderColor": color, "borderWidth": 1, "borderRadius": 2}
        ]},
        "options": _options(mode, label),
    }
    return _chart_component(chart_id, config, mode)


def bar_line_chart(chart_id, labels, bar_data, line_data, bar_label, line_label,
                   bar_color=TEAL, line_color=BLUE, mode="grid"):
    """Bars with a secondary-axis overlay line (e.g. margin %)."""
    options = _options(mode, bar_label, legend=True)
    options["scales"]["y"]["position"] = "left"
    options["scales"]["y1"] = {
        "ticks": {"color": line_color}, "grid": {"drawOnChartArea": False},
        "position": "right",
    }
    config = {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [
                {"type": "bar", "label": bar_label, "data": bar_data,
                 "backgroundColor": bar_color + "CC", "borderColor": bar_color,
                 "borderWidth": 1, "borderRadius": 2, "yAxisID": "y"},
                {"type": "line", "label": line_label, "data": line_data,
                 "borderColor": line_color, "backgroundColor": "transparent",
                 "pointRadius": 0, "tension": 0.3, "yAxisID": "y1",
                 "borderWidth": 2},
            ]
        },
        "options": options,
    }
    return _chart_component(chart_id, config, mode)


def area_chart(chart_id, labels, values, label, color=TEAL, mode="grid"):
    config = {
        "type": "line",
        "data": {"labels": labels, "datasets": [{
            "label": label, "data": values,
            "borderColor": color, "backgroundColor": color + "22",
            "fill": True, "pointRadius": 0, "tension": 0.2,
            "borderWidth": 2
        }]},
        "options": _options(mode, label),
    }
    return _chart_component(chart_id, config, mode)


def multi_line_chart(chart_id, labels, datasets_config, mode="grid"):
    datasets = []
    for ds in datasets_config:
        datasets.append({
            "type": "line", "label": ds["label"], "data": ds["data"],
            "borderColor": ds["color"], "backgroundColor": ds.get("fill", "transparent"),
            "fill": ds.get("fill_to", False),
            "pointRadius": ds.get("point_radius", 2), "tension": 0.3,
            "borderWidth": ds.get("border_width", 2),
            "borderDash": ds.get("dash", []),
        })
    config = {
        "type": "line",
        "data": {"labels": labels, "datasets": datasets},
        "options": _options(mode, legend=True),
    }
    return _chart_component(chart_id, config, mode)


def stacked_bar_chart(chart_id, labels, datasets_config, mode="grid", legend=None):
    datasets = []
    for ds in datasets_config:
        datasets.append({
            "label": ds["label"], "data": ds["data"],
            "backgroundColor": ds["color"] + "CC", "borderColor": ds["color"],
            "borderWidth": 1, "borderRadius": 2
        })
    config = {
        "type": "bar",
        "data": {"labels": labels, "datasets": datasets},
        "options": _options(mode, stacked=True, legend=legend),
    }
    return _chart_component(chart_id, config, mode)


def horizontal_bar_chart(chart_id, labels, values, colors, title="", mode="grid"):
    options = _options(mode, legend=False)
    options["indexAxis"] = "y"
    # Horizontal bars use the y-axis for their categories.  Chart.js index
    # interaction defaults to x, which otherwise selects a bar by value rather
    # than by the stock row under the pointer.
    options["interaction"] = {"mode": "index", "axis": "y", "intersect": False}
    options["scales"] = {
        "x": {"ticks": {"color": MUTED}, "grid": {"color": GRID_COLOR}},
        "y": {"ticks": {"color": TEXT_COLOR}, "grid": {"display": False}},
    }
    config = {
        "type": "bar",
        "data": {"labels": labels, "datasets": [
            {"label": title, "data": values, "backgroundColor": colors,
             "borderRadius": 4, "hoverBorderColor": TEXT_COLOR,
             "hoverBorderWidth": 2}
        ]},
        "options": options,
    }
    return _chart_component(chart_id, config, mode)


# ---------------------------------------------------------------------------
# Metric-aware builders (replacing the old Plotly pipeline)
# ---------------------------------------------------------------------------

def ttm_bar_chart(chart_id, rows, metric, mode="grid", label=None):
    """TTM bar series; detail mode adds the margin-% overlay line when the
    metric has one (PRD: Revenue/EBITDA/FCF charts carry a secondary axis).
    Pass `label` to override the bar series label (e.g. for Qtrly/Annual views)."""
    if not rows:
        return empty_chart("No TTM data available.")
    meta = METRIC_META.get(metric, {"label": metric, "color": TEAL})
    bar_label = label or meta["label"]
    labels = [r["fiscal_qtr"] for r in rows]
    decimals = 2 if metric == "eps" else 0
    values = [round(r["value"], decimals) if r.get("value") is not None else None for r in rows]

    margin_label = meta.get("margin_label")
    margins = [r.get("margin") for r in rows]
    if mode == "detail" and margin_label and any(m is not None for m in margins):
        return bar_line_chart(
            chart_id, labels, values,
            [round(m, 1) if m is not None else None for m in margins],
            bar_label, margin_label,
            bar_color=meta["color"], mode=mode,
        )
    return bar_chart(chart_id, labels, values, bar_label, meta["color"], mode)


def cash_debt_chart(chart_id, rows, mode="grid"):
    if not rows:
        return empty_chart("No cash and debt data available.")
    labels = [r["fiscal_qtr"] for r in rows]
    return stacked_bar_chart(chart_id, labels, [
        {"label": "Cash ($M)", "data": [round(r.get("cash") or 0, 0) for r in rows], "color": TEAL},
        {"label": "Debt ($M)", "data": [round(-(r.get("debt") or 0), 0) for r in rows], "color": RED},
    ], mode=mode, legend=True)


def segment_chart(chart_id, rows, mode="grid"):
    if not rows:
        return empty_chart("No segment data available.")
    labels = sorted({r["fiscal_qtr"] for r in rows})
    segments = sorted({r["segment"] for r in rows})
    datasets = [
        {"label": seg, "color": SERIES_COLORS[i % len(SERIES_COLORS)],
         "data": [round(next((r["revenue"] for r in rows
                              if r["fiscal_qtr"] == q and r["segment"] == seg), 0) or 0, 0)
                  for q in labels]}
        for i, seg in enumerate(segments)
    ]
    return stacked_bar_chart(chart_id, labels, datasets, mode=mode, legend=True)


def pe_cloud_chart(chart_id, history, mode="grid"):
    """P/E line over shaded ±1σ / ±2σ bands around the long-run mean."""
    if not history:
        return empty_chart("No P/E history available.")
    labels = [d[:7] for d in history["dates"]]  # YYYY-MM
    mean, std = history["mean"], history["std"]
    n = len(labels)
    const = lambda v: [round(v, 2)] * n
    # Band shading uses fill:"-1" (fill to the previous dataset): the -2σ
    # dataset fills up to +2σ, then -1σ fills up to +1σ on top of it.
    datasets = [
        {"type": "line", "label": "+2σ", "data": const(mean + 2 * std),
         "borderColor": RED + "55", "borderWidth": 1, "pointRadius": 0,
         "borderDash": [2, 4], "fill": False},
        {"type": "line", "label": "−2σ", "data": const(max(mean - 2 * std, 0)),
         "borderColor": RED + "55", "borderWidth": 1, "pointRadius": 0,
         "borderDash": [2, 4], "fill": "-1", "backgroundColor": RED + "0D"},
        {"type": "line", "label": "+1σ", "data": const(mean + std),
         "borderColor": TEAL + "77", "borderWidth": 1, "pointRadius": 0,
         "borderDash": [4, 4], "fill": False},
        {"type": "line", "label": "−1σ", "data": const(mean - std),
         "borderColor": TEAL + "77", "borderWidth": 1, "pointRadius": 0,
         "borderDash": [4, 4], "fill": "-1", "backgroundColor": TEAL + "14"},
        {"type": "line", "label": "Mean", "data": const(mean),
         "borderColor": SLATE, "borderWidth": 1, "pointRadius": 0,
         "borderDash": [6, 4], "fill": False},
        {"type": "line", "label": "P/E", "data": history["pe"],
         "borderColor": AMBER, "borderWidth": 2, "pointRadius": 0,
         "tension": 0.2, "fill": False},
    ]
    config = {
        "type": "line",
        "data": {"labels": labels, "datasets": datasets},
        "options": _options(mode, "P/E (trailing)", legend=True),
    }
    return _chart_component(chart_id, config, mode)
