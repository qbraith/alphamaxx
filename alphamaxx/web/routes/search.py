"""Ticker/company search autocomplete API."""

from __future__ import annotations

import json

from fasthtml.common import A, Div, Span
from starlette.responses import Response

from alphamaxx.data import get_company_by_ticker, search_companies


def register(app, rt):

    @rt("/api/search/open")
    def open_ticker(q: str = ""):
        """Navigate only when Enter submits an exact ticker in the local DB."""
        company = get_company_by_ticker(q.strip()) if q.strip() else None
        if not company:
            return Response(status_code=204)

        path = f"/stock/{company['ticker']}"
        location = json.dumps({
            "path": path,
            "target": "#page-content",
            "swap": "outerHTML",
        })
        return Response(status_code=204, headers={
            "HX-Location": location,
            "HX-Trigger": "tickerSearchAccepted",
        })

    @rt("/api/search")
    def get(q: str = ""):
        if not q:
            return ""
        results = search_companies(q, limit=10)
        if not results:
            return Div("No results found", cls="search-result-item muted-text")

        return Div(*[
            A(
                Span(r["ticker"], cls="search-result-ticker"),
                Span(r["name"], cls="search-result-name"),
                cls="search-result-item",
                href=f"/stock/{r['ticker']}",
                hx_get=f"/stock/{r['ticker']}",
                hx_target="#page-content",
                hx_swap="outerHTML",
                hx_push_url=f"/stock/{r['ticker']}",
            ) for r in results
        ])
