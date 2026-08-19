"""Yahoo-only weekly price refreshes for legacy Portfolio members."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from alphamaxx.services import price_updater
from alphamaxx.data.prices import upsert_price_bar


def _history(values):
    index = pd.to_datetime(["2026-07-13", "2026-07-20", "2026-07-24"])
    return pd.DataFrame({
        "Open": values,
        "High": values,
        "Low": values,
        "Close": values,
        "Adj Close": values,
        "Volume": [100, 200, 300],
    }, index=index)


def test_one_ticker_refresh_updates_weekly_rows_and_momentum(monkeypatch):
    class Ticker:
        info = {}

        def history(self, **kwargs):
            assert kwargs == {
                "period": "1mo", "interval": "1wk", "auto_adjust": False,
            }
            return _history([99.0, 100.0, 101.25])

    writes = []
    momentum = []
    monkeypatch.setattr(
        price_updater, "upsert_price_bar",
        lambda *args: writes.append(args),
    )
    monkeypatch.setattr(
        price_updater, "refresh_momentum", momentum.append,
    )
    monkeypatch.setattr(
        price_updater, "upsert_valuation_cache", lambda *args: None,
    )

    assert price_updater._update_company_price_unlocked(
        880073, "QETF", SimpleNamespace(Ticker=lambda _ticker: Ticker()), pd
    )
    assert [row[1] for row in writes] == [
        pd.Timestamp("2026-07-20").date(),
        pd.Timestamp("2026-07-24").date(),
    ]
    assert writes[-1][6] == 101.25
    assert momentum == [880073]


def test_empty_or_all_nan_yahoo_history_preserves_cached_state(monkeypatch):
    writes = []
    momentum = []
    monkeypatch.setattr(
        price_updater, "upsert_price_bar",
        lambda *args: writes.append(args),
    )
    monkeypatch.setattr(
        price_updater, "refresh_momentum", momentum.append,
    )

    class EmptyTicker:
        def history(self, **kwargs):
            return pd.DataFrame()

    assert not price_updater._update_company_price_unlocked(
        880073, "QETF", SimpleNamespace(Ticker=lambda _ticker: EmptyTicker()), pd
    )

    class NanTicker:
        def history(self, **kwargs):
            return _history([float("nan")] * 3)

    assert not price_updater._update_company_price_unlocked(
        880073, "QETF", SimpleNamespace(Ticker=lambda _ticker: NanTicker()), pd
    )
    assert writes == []
    assert momentum == []


def test_routine_price_refresh_preserves_existing_split_marker(db):
    permno = 881111
    price_date = pd.Timestamp("2026-07-20").date()
    db.execute(
        "INSERT OR REPLACE INTO companies (permno, ticker, name) "
        "VALUES (?, 'SYN1', 'Synthetic One')",
        [permno],
    )
    db.execute("DELETE FROM prices WHERE permno = ?", [permno])
    upsert_price_bar(
        permno, price_date, 10, 11, 9, 10, 10, 100, split_factor=2.0,
    )
    upsert_price_bar(permno, price_date, 20, 22, 18, 20, 20, 200)
    row = db.execute(
        "SELECT close, split_factor FROM prices WHERE permno = ? AND price_date = ?",
        [permno, price_date],
    ).fetchone()
    assert row == (20.0, 2.0)


def test_actual_split_date_replaces_legacy_weekly_marker(db):
    from datetime import date

    from alphamaxx.data import upsert_stock_splits

    permno = 999004
    db.execute(
        "INSERT OR REPLACE INTO companies (permno, ticker, name) "
        "VALUES (?, 'SPLT', 'Synthetic Split Corp')",
        [permno],
    )
    db.execute("DELETE FROM prices WHERE permno = ?", [permno])
    db.execute("DELETE FROM stock_splits WHERE permno = ?", [permno])
    db.execute(
        "INSERT INTO prices "
        "(permno, price_date, close, split_factor) VALUES (?, ?, 10, 2)",
        [permno, date(2026, 8, 10)],
    )
    db.execute(
        "INSERT INTO stock_splits VALUES (?, ?, 2)",
        [permno, date(2026, 8, 10)],
    )

    assert upsert_stock_splits(
        permno, [(date(2026, 8, 12), 2.0)],
    ) == 1
    assert db.execute(
        "SELECT split_date, split_factor FROM stock_splits "
        "WHERE permno = ? ORDER BY split_date",
        [permno],
    ).fetchall() == [(date(2026, 8, 12), 2.0)]
    assert db.execute(
        "SELECT split_factor FROM prices WHERE permno = ?", [permno],
    ).fetchone() == (1.0,)


def test_legacy_split_migration_is_one_way_and_idempotent(db):
    from datetime import date

    from alphamaxx.data import init_db

    permno = 999005
    split_date = date(2026, 7, 6)
    db.execute(
        "INSERT OR REPLACE INTO companies (permno, ticker, name) "
        "VALUES (?, 'LSPL', 'Legacy Split Corp')",
        [permno],
    )
    db.execute("DELETE FROM prices WHERE permno = ?", [permno])
    db.execute("DELETE FROM stock_splits WHERE permno = ?", [permno])
    db.execute(
        "INSERT INTO prices "
        "(permno, price_date, close, split_factor) VALUES (?, ?, 10, 3)",
        [permno, split_date],
    )

    init_db()
    init_db()
    assert db.execute(
        "SELECT split_date, split_factor FROM stock_splits WHERE permno = ?",
        [permno],
    ).fetchall() == [(split_date, 3.0)]
    assert db.execute(
        "SELECT split_factor FROM prices WHERE permno = ?", [permno],
    ).fetchone() == (1.0,)
