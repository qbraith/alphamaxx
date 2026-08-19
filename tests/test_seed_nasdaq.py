"""The WRDS NASDAQ seeder initializes and upserts the public schema."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import scripts.seed_nasdaq500 as seed_module


def test_seed_honors_limit_and_uses_existing_company_schema(db, monkeypatch):
    rows = pd.DataFrame([
        {"permno": 881201, "ticker": "SNA", "name": "Synthetic Alpha",
         "market_cap_usd": 2_000_000.0},
        {"permno": 881202, "ticker": "SNB", "name": "Synthetic Beta",
         "market_cap_usd": 1_000_000.0},
    ])

    class StubConnection:
        def __init__(self):
            self.query = ""
            self.params = None
            self.closed = False

        def raw_sql(self, query, **kwargs):
            self.query = query
            self.params = kwargs.get("params")
            return rows

        def close(self):
            self.closed = True

    stub = StubConnection()
    monkeypatch.setattr(
        seed_module, "wrds",
        SimpleNamespace(Connection=lambda **_kwargs: stub),
    )
    seed_module.seed(limit=2)

    assert "LIMIT %(limit)s" in stub.query
    assert stub.params == {"limit": 2}
    assert stub.closed
    stored = db.execute(
        "SELECT permno, ticker, in_watchlist FROM companies "
        "WHERE permno IN (881201, 881202) ORDER BY permno"
    ).fetchall()
    assert stored == [(881201, "SNA", True), (881202, "SNB", True)]


def test_seed_rejects_unbounded_limit_before_network(monkeypatch):
    monkeypatch.setattr(
        seed_module, "wrds",
        SimpleNamespace(Connection=lambda **_kwargs: pytest.fail("network attempted")),
    )
    with pytest.raises(ValueError, match="between 1 and 5000"):
        seed_module.seed(limit=5001)
