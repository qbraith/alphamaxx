"""Route smoke tests: every registered page renders 200 against the temp DB.

Catches import/registration errors and None-handling regressions cheaply.
External-network pages (earnings, indices) only read caches on the request
path, so these tests never block on the network.
"""

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

PAGES = [
    "/",
    "/watchlist",
    "/portfolio",
    "/earnings-calendar",
    "/econ-calendar",
    "/econ-calendar?view=grid",
    "/dcf",
    "/dcf?ticker=TEST",
    "/transcripts",
    "/data-queue",
    "/api/status-bar",
    "/api/search?q=TEST",
]


@pytest.fixture(scope="module")
def client(db):
    from alphamaxx.web.app import app
    with TestClient(app, base_url="http://localhost") as c:
        yield c


@pytest.mark.parametrize("path", PAGES)
def test_page_renders(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} -> {resp.status_code}"


def test_local_request_boundary_rejects_host_and_cross_site_requests(client):
    assert client.get("/", headers={"host": "attacker.example"}).status_code == 400
    assert client.get("/", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    response = client.post(
        "/api/dcf/calc",
        data={"eps": "1", "growth": "5", "multiple": "10", "desired": "8"},
        headers={"origin": "https://attacker.example"},
    )
    assert response.status_code == 403


def test_local_request_boundary_handles_ipv6_and_rejects_wildcards(client):
    response = client.get(
        "/", headers={"host": "[::1]:8000", "origin": "http://[::1]:8000"},
    )
    assert response.status_code == 200

    from alphamaxx.web.security import ExactHostMiddleware
    with pytest.raises(ValueError, match="without wildcards"):
        ExactHostMiddleware(lambda *_: None, ["*"])


def test_local_request_boundary_allows_same_origin_and_sets_headers(client):
    response = client.post(
        "/api/dcf/calc",
        data={"eps": "1", "growth": "5", "multiple": "10", "desired": "8"},
        headers={"origin": "http://localhost"},
    )
    assert response.status_code == 200
    page = client.get("/")
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


@pytest.mark.parametrize("path", ["/", "/watchlist", "/data-queue"])
def test_htmx_fragment_renders(client, path):
    resp = client.get(path, headers={"hx-request": "true"})
    assert resp.status_code == 200
    # Fragments must not include the full app shell.
    assert "command-bar" not in resp.text


def test_stock_page_known_and_unknown(client, synthetic_company):
    resp = client.get("/stock/TEST")
    assert resp.status_code == 200
    assert "TEST" in resp.text

    resp = client.get("/stock/ZZZZNOPE")
    assert resp.status_code == 200
    assert "not found" in resp.text


def test_search_selection_closes_persistent_popup(client, synthetic_company):
    resp = client.get("/api/search?q=TEST")
    assert resp.status_code == 200
    assert 'class="search-result-item"' in resp.text
    assert 'hx-get="/stock/TEST"' in resp.text
    assert 'hx-target="#page-content"' in resp.text
    assert "after-swap" not in resp.text

    app_js = (Path(__file__).parents[1] / "static" / "app.js").read_text()
    assert 'closest("a.search-result-item")' in app_js
    assert "window.setTimeout(resetSearch, 0)" in app_js
    assert 'window.htmx.trigger(search, "htmx:abort")' in app_js


def test_enter_on_exact_ticker_navigates_with_htmx(client, synthetic_company):
    shell = client.get("/")
    assert 'action="/api/search/open"' in shell.text
    assert 'hx-get="/api/search/open"' in shell.text
    assert 'hx-swap="none"' in shell.text

    resp = client.get(
        "/api/search/open", params={"q": "  test  "},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 204
    assert resp.headers["hx-trigger"] == "tickerSearchAccepted"
    assert json.loads(resp.headers["hx-location"]) == {
        "path": "/stock/TEST",
        "target": "#page-content",
        "swap": "outerHTML",
    }

    app_js = (Path(__file__).parents[1] / "static" / "app.js").read_text()
    assert 'document.addEventListener("tickerSearchAccepted", resetSearch)' in app_js


@pytest.mark.parametrize("query", ["", "NOTREAL", "Test Corporation"])
def test_enter_on_non_ticker_is_a_noop(client, synthetic_company, query):
    resp = client.get(
        "/api/search/open", params={"q": query},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 204
    assert "hx-location" not in resp.headers
    assert "hx-trigger" not in resp.headers


def test_chart_api(client, synthetic_company):
    from alphamaxx.data import refresh_ttm
    refresh_ttm(synthetic_company)
    for metric in ("revenue", "eps", "cash_debt", "price", "dividends"):
        resp = client.get(f"/api/chart/{synthetic_company}/{metric}?period=ttm")
        assert resp.status_code == 200, f"{metric} -> {resp.status_code}"


def test_dcf_calc_roundtrip(client):
    resp = client.post(
        "/api/dcf/calc",
        data={"eps": "5.00", "growth": "15.0", "multiple": "20.0", "desired": "10.0"},
    )
    assert resp.status_code == 200
    assert "Discounted Base Value" in resp.text
    assert "data-chart-config" in resp.text


def test_dcf_rejects_minus_one_hundred_percent_discount(client):
    response = client.post(
        "/api/dcf/calc",
        data={"eps": "5", "growth": "10", "multiple": "15", "desired": "-100"},
    )
    assert response.status_code == 422
    assert "greater than -100%" in response.text


def test_stock_page_shows_forward_pe_not_trailing(client, synthetic_company):
    from alphamaxx.data import upsert_valuation_cache
    upsert_valuation_cache("TEST", 99.9, 2.0, 18.3)
    resp = client.get("/stock/TEST")
    assert resp.status_code == 200
    assert "Fwd P/E" in resp.text
    assert "18.3x" in resp.text
    assert "99.9x" not in resp.text  # trailing value must not leak into the chip


def test_watchlist_and_portfolio_show_both_pe_columns(client, synthetic_company, monkeypatch):
    import alphamaxx.web.routes.lists as lists_routes
    from alphamaxx.data import set_portfolio, set_watchlist
    set_watchlist(synthetic_company, True)
    set_portfolio(synthetic_company, True)
    monkeypatch.setattr(
        lists_routes, "fetch_yf_valuations",
        lambda tickers: {"TEST": {"pe": 21.4, "peg": 1.5, "forward_pe": 18.3}},
    )
    for path in ("/watchlist", "/portfolio"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        assert "Fwd P/E" in resp.text, f"{path} missing Fwd P/E header"
        assert "21.4x" in resp.text, f"{path} missing trailing P/E value"
        assert "18.3x" in resp.text, f"{path} missing forward P/E value"
    set_watchlist(synthetic_company, False)
    set_portfolio(synthetic_company, False)


def test_adding_portfolio_member_starts_immediate_yahoo_refresh(
        client, synthetic_company, monkeypatch):
    import alphamaxx.services.price_updater as price_updater
    from alphamaxx.data import set_portfolio

    set_portfolio(synthetic_company, False)
    calls = []
    monkeypatch.setattr(
        price_updater, "start_ticker_price_refresh",
        lambda permno, ticker: calls.append((permno, ticker)),
    )
    response = client.post("/api/portfolio/add", data={"ticker": "test"})
    assert response.status_code == 200
    assert "price refresh started" in response.text
    assert calls == [(synthetic_company, "TEST")]
    set_portfolio(synthetic_company, False)
