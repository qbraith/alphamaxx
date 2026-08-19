"""Chart builders emit canvases whose data-chart-config is valid JSON."""

import json

from fasthtml.common import to_xml

from alphamaxx.web.charts import (
    bar_chart,
    bar_line_chart,
    cash_debt_chart,
    horizontal_bar_chart,
    pe_cloud_chart,
    segment_chart,
    ttm_bar_chart,
)


def _config(component) -> dict:
    html = to_xml(component)
    marker = "data-chart-config="
    i = html.index(marker) + len(marker)
    quote = html[i]
    raw = html[i + 1:html.index(quote, i + 1)]
    import html as html_mod
    return json.loads(html_mod.unescape(raw))


def test_bar_chart_config():
    cfg = _config(bar_chart("t1", ["Q1", "Q2"], [1, 2], "Rev", "#F5A623"))
    assert cfg["type"] == "bar"
    assert cfg["data"]["labels"] == ["Q1", "Q2"]
    assert len(cfg["data"]["datasets"]) == 1
    assert cfg["options"]["animation"] is False


def test_horizontal_bar_hover_tracks_rows_and_outlines_active_bar():
    cfg = _config(horizontal_bar_chart(
        "dips", ["AAA", "BBB"], [-10.0, 5.0], ["#F43F5E", "#14B8A6"],
        "% from 200d SMA",
    ))
    assert cfg["options"]["indexAxis"] == "y"
    assert cfg["options"]["interaction"] == {
        "mode": "index", "axis": "y", "intersect": False,
    }
    dataset = cfg["data"]["datasets"][0]
    assert dataset["hoverBorderColor"] == "#E2E8F0"
    assert dataset["hoverBorderWidth"] == 2


def test_bar_line_dual_axis():
    cfg = _config(bar_line_chart("t2", ["Q1"], [10], [40.0], "Rev", "Margin %"))
    assert len(cfg["data"]["datasets"]) == 2
    assert cfg["data"]["datasets"][1]["yAxisID"] == "y1"
    assert "y1" in cfg["options"]["scales"]


def test_ttm_bar_detail_includes_margin_overlay():
    rows = [{"fiscal_qtr": "2024Q4", "value": 100.0, "margin": 42.0}]
    cfg = _config(ttm_bar_chart("t3", rows, "revenue", "detail"))
    labels = [d["label"] for d in cfg["data"]["datasets"]]
    assert "Gross Margin %" in labels


def test_ttm_bar_grid_skips_margin():
    rows = [{"fiscal_qtr": "2024Q4", "value": 100.0, "margin": 42.0}]
    cfg = _config(ttm_bar_chart("t4", rows, "revenue", "grid"))
    assert len(cfg["data"]["datasets"]) == 1


def test_cash_debt_negates_debt():
    rows = [{"fiscal_qtr": "2024Q4", "cash": 500.0, "debt": 200.0}]
    cfg = _config(cash_debt_chart("t5", rows))
    debt_ds = next(d for d in cfg["data"]["datasets"] if "Debt" in d["label"])
    assert debt_ds["data"] == [-200]
    assert cfg["options"]["scales"]["x"]["stacked"] is True


def test_segment_chart_stacks_by_segment():
    rows = [
        {"fiscal_qtr": "2024Q4", "segment": "Cloud", "revenue": 100},
        {"fiscal_qtr": "2024Q4", "segment": "Ads", "revenue": 300},
    ]
    cfg = _config(segment_chart("t6", rows))
    assert {d["label"] for d in cfg["data"]["datasets"]} == {"Cloud", "Ads"}


def test_pe_cloud_bands():
    history = {
        "dates": ["2024-01-05", "2024-01-12", "2024-01-19"],
        "pe": [20.0, 22.0, 21.0],
        "mean": 21.0, "std": 1.0, "current": 21.0,
    }
    cfg = _config(pe_cloud_chart("t7", history))
    labels = [d["label"] for d in cfg["data"]["datasets"]]
    assert labels == ["+2σ", "−2σ", "+1σ", "−1σ", "Mean", "P/E"]
    # Band datasets fill to the previous dataset
    assert cfg["data"]["datasets"][1]["fill"] == "-1"
    assert cfg["data"]["datasets"][3]["fill"] == "-1"
