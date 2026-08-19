"""Detail-table builders for chart modal views (QoQ / YoY breakdowns)."""

from __future__ import annotations

from fasthtml.common import Table, Tbody, Td, Th, Thead, Tr

from alphamaxx.data.fundamentals import get_quarterly_series
from alphamaxx.web.charts import METRIC_META
from alphamaxx.web.components import color_cls, fmt_pct, fmt_ratio


def _fmt_val(v):
    """Format a value as T/B/M for display."""
    if v is None: return "—"
    av = abs(v)
    if av >= 1_000_000: return f"${v/1_000_000:.2f}T"
    if av >= 1000: return f"${v/1000:.1f}B"
    return f"${v:.0f}M"


def _pct_from_values(curr, prev):
    if curr is None or prev is None or prev == 0:
        return None
    return (curr - prev) / abs(prev) * 100


def _detail_value(v, metric: str = ""):
    if v is None:
        return "—"
    if metric in ("eps", "dividends"):
        return f"{v:,.2f}"
    return f"{v:,.0f}"


def detail_table(labels, series_rows, col_keys, col_labels):
    """High-density data table over WEEKLY rows (used by the price detail
    modal) with week-over-week and 52-week change columns."""
    header_cells = [Th("Week")] + [Th(l) for l in col_labels] + [Th("WoW"), Th("YoY")]
    rows = []
    for i, (lbl, row) in enumerate(zip(labels, series_rows)):
        vals = [row.get(k) for k in col_keys]
        primary = vals[0]
        wow = "—"
        yoy = "—"
        if primary is not None and i > 0:
            prev_w = series_rows[i-1].get(col_keys[0])
            if prev_w and prev_w != 0:
                wow_v = (primary - prev_w) / abs(prev_w) * 100
                wow = f"{'+'if wow_v>=0 else ''}{wow_v:.1f}%"
        if primary is not None and i >= 52:
            prev_y = series_rows[i-52].get(col_keys[0])
            if prev_y and prev_y != 0:
                yoy_v = (primary - prev_y) / abs(prev_y) * 100
                yoy = f"{'+'if yoy_v>=0 else ''}{yoy_v:.1f}%"
        # Values here are per-share dollars (price), not $M — plain currency.
        cells = [Td(lbl)] + [Td("—" if v is None else f"${v:,.2f}", cls="num") for v in vals] + \
                [Td(wow, cls="num"), Td(yoy, cls="num")]
        rows.append(Tr(*cells))
    return Table(Thead(Tr(*header_cells)), Tbody(*reversed(rows)), cls="data-table detail-table")


def chart_detail_table(rows: list[dict], metric: str, label: str | None = None):
    if label is None:
        label = METRIC_META.get(metric, {}).get("label", "Value")
    body = []
    for r in reversed(rows):
        extra_cells = []
        if metric == "fcf":
            extra_cells = [
                Td(fmt_pct(r.get("fcf_conversion_pct")), cls="num"),
                Td("—" if r.get("fcf_per_share") is None
                   else f"${r['fcf_per_share']:,.2f}", cls="num"),
            ]
        elif metric == "shares":
            extra_cells = [Td(fmt_pct(r.get("three_year_cagr_pct")), cls="num")]
        body.append(Tr(
            Td(r.get("fiscal_qtr") or r.get("report_date") or "—"),
            Td(_detail_value(r.get("value"), metric), cls="num"),
            Td(fmt_pct(r.get("qoq_pct")), cls=f"num {color_cls(r.get('qoq_pct'))}"),
            Td(fmt_pct(r.get("yoy_pct")), cls=f"num {color_cls(r.get('yoy_pct'))}"),
            *extra_cells,
        ))
    extra_headers = []
    if metric == "fcf":
        extra_headers = [Th("FCF Conversion"), Th("FCF / Share")]
    elif metric == "shares":
        extra_headers = [Th("3Y CAGR")]
    return Table(
        Thead(Tr(Th("Date"), Th(label), Th("QoQ %"), Th("YoY %"), *extra_headers)),
        Tbody(*body),
        cls="data-table detail-table",
    )


def cash_debt_detail_table(rows: list[dict]):
    enriched = []
    for row in rows:
        item = dict(row)
        item["net_cash"] = (row["cash"] - row["debt"]
                            if row.get("cash") is not None and row.get("debt") is not None
                            else None)
        enriched.append(item)
    for i, row in enumerate(enriched):
        row["qoq_pct"] = None
        row["yoy_pct"] = None
        if i >= 1:
            row["qoq_pct"] = _pct_from_values(row["net_cash"], enriched[i - 1]["net_cash"])
        if i >= 4:
            row["yoy_pct"] = _pct_from_values(row["net_cash"], enriched[i - 4]["net_cash"])
    return Table(
        Thead(Tr(Th("Date"), Th("Cash ($M)"), Th("Debt ($M)"), Th("Net Cash ($M)"),
                 Th("Net Debt / EBITDA"), Th("QoQ %"), Th("YoY %"))),
        Tbody(*[
            Tr(
                Td(r["fiscal_qtr"]),
                Td(_detail_value(r.get("cash")), cls="num"),
                Td(_detail_value(r.get("debt")), cls="num"),
                Td(_detail_value(r.get("net_cash")), cls="num"),
                Td(fmt_ratio(r.get("net_debt_ebitda")), cls="num"),
                Td(fmt_pct(r.get("qoq_pct")), cls=f"num {color_cls(r.get('qoq_pct'))}"),
                Td(fmt_pct(r.get("yoy_pct")), cls=f"num {color_cls(r.get('yoy_pct'))}"),
            ) for r in reversed(enriched)
        ]),
        cls="data-table detail-table",
    )


def segment_detail_table(rows: list[dict]):
    grouped = {}
    enriched = []
    for row in rows:
        hist = grouped.setdefault(row["segment"], [])
        item = dict(row)
        item["qoq_pct"] = _pct_from_values(item.get("revenue"), hist[-1].get("revenue")) if hist else None
        item["yoy_pct"] = _pct_from_values(item.get("revenue"), hist[-4].get("revenue")) if len(hist) >= 4 else None
        hist.append(item)
        enriched.append(item)
    return Table(
        Thead(Tr(Th("Date"), Th("Segment"), Th("Revenue ($M)"), Th("QoQ %"), Th("YoY %"))),
        Tbody(*[
            Tr(
                Td(r["fiscal_qtr"]),
                Td(r["segment"]),
                Td(_detail_value(r.get("revenue")), cls="num"),
                Td(fmt_pct(r.get("qoq_pct")), cls=f"num {color_cls(r.get('qoq_pct'))}"),
                Td(fmt_pct(r.get("yoy_pct")), cls=f"num {color_cls(r.get('yoy_pct'))}"),
            ) for r in reversed(enriched)
        ]),
        cls="data-table detail-table",
    )


def quarterly_value_series(permno: int, key: str, limit: int | None = None) -> list[dict]:
    series = get_quarterly_series(permno, n_quarters=limit)
    series.reverse()
    rows = [
        {"fiscal_qtr": s["fiscal_qtr"], "report_date": str(s["report_date"]), "value": s.get(key)}
        for s in series
    ]
    for i, row in enumerate(rows):
        row["qoq_pct"] = _pct_from_values(row.get("value"), rows[i - 1].get("value")) if i >= 1 else None
        row["yoy_pct"] = _pct_from_values(row.get("value"), rows[i - 4].get("value")) if i >= 4 else None
    return rows
