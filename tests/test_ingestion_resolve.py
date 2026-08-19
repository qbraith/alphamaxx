"""Ticker-resolution guard: CRSP recycles ticker symbols, so when yfinance
reports a company name that clearly conflicts with CRSP's, the (stale) CRSP
identity must not be returned. These tests stub WRDS and yfinance — no network."""

import pandas as pd
import pytest

import alphamaxx.services.ingestion as ingestion_module
from alphamaxx.services.ingestion import (
    COMPUSTAT_COLS,
    CompanyIdentityMismatch,
    WRDSIngester,
    _name_tokens,
    _names_conflict,
    _explicit_etf_name,
    _split_events_from_series,
    _wrds_username,
)


class _StubWRDS:
    """Returns a fixed one-row msenames result regardless of the query."""

    def __init__(self, df: pd.DataFrame):
        self._df = df
        self.calls = 0
        self.queries = []
        self.parameters = []

    def raw_sql(self, sql, **kwargs):
        self.calls += 1
        self.queries.append(sql)
        self.parameters.append(kwargs.get("params"))
        return self._df


def _row(comnam="OLD RECYCLED CO", permno=771234, ticker="XQRS", exchcd=3):
    return pd.DataFrame(
        [{"permno": permno, "ticker": ticker, "comnam": comnam, "exchcd": exchcd}]
    )


def _ingester(df, yf_name):
    ing = WRDSIngester()
    ing.db_wrds = _StubWRDS(df)
    ing._yf_name_calls = 0

    def fake_name(ticker):
        ing._yf_name_calls += 1
        return yf_name

    ing._yf_name = fake_name  # type: ignore[method-assign]
    return ing


# ---- name comparison helpers -------------------------------------------------

def test_name_tokens_strips_corporate_noise():
    assert _name_tokens("Apple Inc.") == {"APPLE"}
    assert _name_tokens("Meta Platforms, Inc.") == {"META", "PLATFORMS"}
    assert _name_tokens("LEGACY ORCHARD SERIES TRUST") == {"LEGACY", "ORCHARD"}


def test_names_conflict():
    assert _names_conflict("LEGACY ORCHARD SERIES TRUST",
                           "Nova Robotics Labs Corp") is True
    # Same company in different casing/punctuation -> no conflict.
    assert _names_conflict("APPLE INC", "Apple Inc.") is False
    assert _names_conflict("META PLATFORMS INC", "Meta Platforms, Inc.") is False
    # Partial overlap (shared significant token) -> not a conflict.
    assert _names_conflict("ACME CORP", "Acme Energy Inc") is False
    # Missing yfinance name -> can't conflict.
    assert _names_conflict("ANYTHING CO", "") is False


def test_explicit_etf_name_is_narrow():
    assert _explicit_etf_name("E T F SERIES SOLUTIONS")
    assert _explicit_etf_name("Example Quantum Strategy ETF")
    assert not _explicit_etf_name("LEGACY ORCHARD SERIES TRUST")
    assert not _explicit_etf_name("Nova Robotics Labs Corp")


def test_split_events_keep_actual_non_monday_date():
    splits = pd.Series(
        [2.0], index=pd.to_datetime(["2026-08-12 09:30:00-04:00"]),
    )
    assert _split_events_from_series(splits) == [
        (pd.Timestamp("2026-08-12").date(), 2.0),
    ]


def test_crsp_weekly_buckets_start_on_monday(db):
    permno = 881112
    db.execute(
        "INSERT OR REPLACE INTO companies (permno, ticker, name) "
        "VALUES (?, 'SYN2', 'Synthetic Two')",
        [permno],
    )
    db.execute("DELETE FROM prices WHERE permno = ?", [permno])
    dates = pd.to_datetime([
        "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06",
        "2026-08-07", "2026-08-10", "2026-08-11",
    ])
    values = list(range(10, 17))
    frame = pd.DataFrame({
        "date": dates,
        "prc": values,
        "openprc": values,
        "bidlo": [value - 1 for value in values],
        "askhi": [value + 1 for value in values],
        "vol": [100] * len(values),
        "cfacpr": [1.0] * len(values),
    })
    ing = WRDSIngester()
    ing.db_wrds = _StubWRDS(frame)
    assert ing.ingest_prices_crsp(permno, "SYN2") == 2
    rows = db.execute(
        "SELECT price_date, close, volume FROM prices "
        "WHERE permno = ? ORDER BY price_date",
        [permno],
    ).fetchall()
    assert rows == [
        (pd.Timestamp("2026-08-03").date(), 14.0, 500),
        (pd.Timestamp("2026-08-10").date(), 16.0, 200),
    ]


def test_wrds_username_comes_from_pgpass_for_noninteractive_jobs(
    tmp_path, monkeypatch
):
    pgpass = tmp_path / "pgpass"
    pgpass.write_text(
        "wrds-pgdata.wharton.upenn.edu:9737:wrds:wrds_account:secret\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("WRDS_USERNAME", raising=False)
    monkeypatch.delenv("PGUSER", raising=False)
    monkeypatch.setenv("PGPASSFILE", str(pgpass))
    assert _wrds_username() == "wrds_account"


# ---- resolver guard ----------------------------------------------------------

def test_matching_name_resolves():
    ing = _ingester(_row(comnam="APPLE INC", ticker="AAPL"), yf_name="Apple Inc.")
    out = ing.resolve_company_for_ticker("AAPL")
    assert out is not None and out["permno"] == 771234
    assert out["exchange"] == "NASDAQ"


def test_conflicting_name_returns_none():
    ing = _ingester(_row(comnam="LEGACY ORCHARD SERIES TRUST"),
                    yf_name="Nova Robotics Labs Corp")
    assert ing.resolve_company_for_ticker("XQRS") is None
    assert ing._yf_name_calls == 1


def test_branded_etf_name_may_differ_from_current_crsp_registrant():
    ing = _ingester(
        _row(
            comnam="E T F SERIES SOLUTIONS",
            permno=880073,
            ticker="QETF",
            exchcd=3,
        ),
        yf_name="Example Quantum Strategy ETF",
    )
    out = ing.resolve_company_for_ticker("QETF")
    assert out == {
        "permno": 880073,
        "ticker": "QETF",
        "name": "E T F SERIES SOLUTIONS",
        "exchange": "NASDAQ",
    }


def test_source_filtered_current_record_survives_missing_yfinance_name():
    # The SQL has already excluded historical-only rows. A temporary yfinance
    # outage must not reject the source-current CRSP identity.
    ing = _ingester(_row(comnam="CURRENT CO"), yf_name=None)
    out = ing.resolve_company_for_ticker("XYZ")
    assert out is not None and out["permno"] == 771234


def test_resolver_uses_crsp_source_vintage_instead_of_wall_clock():
    ing = _ingester(_row(comnam="DEMO SYSTEMS INC", permno=777123), yf_name="Demo Systems Inc")
    assert ing.resolve_permno_for_ticker("DEMO") == 777123
    sql = ing.db_wrds.queries[0]
    assert "source_vintage" in sql
    assert "INTERVAL '31 days'" in sql
    assert "CURRENT_DATE" not in sql
    assert "%(ticker)s" in sql
    assert ing.db_wrds.parameters[0] == {"ticker": "DEMO"}


def test_permno_resolver_mirrors_company_resolver():
    conflict = _ingester(_row(comnam="LEGACY ORCHARD SERIES TRUST"),
                         yf_name="Nova Robotics Labs Corp")
    assert conflict.resolve_permno_for_ticker("XQRS") is None
    ok = _ingester(_row(comnam="APPLE INC", ticker="AAPL"), yf_name="Apple Inc.")
    assert ok.resolve_permno_for_ticker("AAPL") == 771234


def test_empty_crsp_result_returns_none():
    ing = _ingester(pd.DataFrame(columns=["permno", "ticker", "comnam", "exchcd"]),
                    yf_name="Whatever Corp")
    assert ing.resolve_company_for_ticker("NOPE") is None
    assert ing.resolve_permno_for_ticker("NOPE") is None


def test_existing_company_permno_mismatch_is_rejected():
    ing = _ingester(
        _row(comnam="DEMO SYSTEMS INC", permno=777123, ticker="DEMO", exchcd=1),
        yf_name="Demo Systems Inc.",
    )

    with pytest.raises(CompanyIdentityMismatch, match="777122.*777123"):
        ing.validate_company_identity(777122, "DEMO")


def test_full_ingestion_aborts_before_any_component_on_identity_mismatch(monkeypatch):
    ing = _ingester(
        _row(comnam="DEMO SYSTEMS INC", permno=777123, ticker="DEMO", exchcd=1),
        yf_name="Demo Systems Inc.",
    )
    calls = []
    monkeypatch.setattr(ing, "ingest_fundamentals", lambda *a, **k: calls.append("fundamentals"))
    monkeypatch.setattr(ing, "ingest_prices", lambda *a, **k: calls.append("prices"))
    monkeypatch.setattr(ing, "ingest_dividends", lambda *a, **k: calls.append("dividends"))
    monkeypatch.setattr(ing, "ingest_profile", lambda *a, **k: calls.append("profile"))

    with pytest.raises(CompanyIdentityMismatch):
        ing.ingest_company(777122, "DEMO")

    assert calls == []


def test_current_branded_etf_runs_price_components_without_name_match(
        monkeypatch):
    ing = _ingester(
        _row(
            comnam="E T F SERIES SOLUTIONS",
            permno=880073,
            ticker="QETF",
            exchcd=3,
        ),
        yf_name="Example Quantum Strategy ETF",
    )
    calls = []
    monkeypatch.setattr(
        ing, "ingest_fundamentals",
        lambda *args, **kwargs: calls.append("fundamentals"),
    )
    monkeypatch.setattr(
        ing, "ingest_prices", lambda *args, **kwargs: calls.append("prices"),
    )
    monkeypatch.setattr(
        ing, "ingest_dividends", lambda *args, **kwargs: calls.append("dividends"),
    )
    monkeypatch.setattr(
        ing, "ingest_profile", lambda *args, **kwargs: calls.append("profile"),
    )
    monkeypatch.setattr(
        ingestion_module, "refresh_momentum",
        lambda permno: calls.append("momentum"),
    )
    monkeypatch.setattr(
        ingestion_module, "refresh_ingestion_status",
        lambda permno: calls.append("status"),
    )
    monkeypatch.setattr(
        ingestion_module, "get_conn",
        lambda: type("_Conn", (), {"commit": lambda self: None})(),
    )

    ing.ingest_company(880073, "QETF")
    assert calls == [
        "fundamentals", "prices", "dividends", "profile", "momentum", "status",
    ]


def test_unresolvable_ticker_skips_fundamentals_but_keeps_price_components(
        monkeypatch):
    """A deliberately suppressed recycled-ticker match is not a conflict.

    ``_resolve_crsp_row`` returns None when the recycled-ticker guard rejects
    an old trust. That must not abort the company run, or the synthetic-PERMNO
    price path can never execute.
    """
    ing = _ingester(
        _row(comnam="OLD RECYCLED TRUST", permno=771234, ticker="XQRS", exchcd=3),
        yf_name="Nova Robotics Labs",
    )
    calls = []
    monkeypatch.setattr(ing, "ingest_prices", lambda *a, **k: calls.append("prices"))
    monkeypatch.setattr(ing, "ingest_dividends", lambda *a, **k: calls.append("dividends"))
    monkeypatch.setattr(ing, "ingest_profile", lambda *a, **k: calls.append("profile"))
    monkeypatch.setattr(
        ingestion_module, "refresh_momentum", lambda permno: calls.append("momentum"),
    )
    monkeypatch.setattr(
        ingestion_module, "refresh_ingestion_status",
        lambda permno: calls.append("status"),
    )
    monkeypatch.setattr(
        ingestion_module, "get_conn",
        lambda: type("_Conn", (), {"commit": lambda self: None})(),
    )

    assert ing.validate_company_identity(990024, "XQRS") is None

    ing.ingest_company(990024, "XQRS")
    # Fundamentals are skipped (no CRSP identity to link a GVKEY through), but
    # every price-side component still runs.
    assert calls == ["prices", "dividends", "profile", "momentum", "status"]


def test_synthetic_permno_without_identity_writes_no_fundamentals(monkeypatch):
    ing = _ingester(
        pd.DataFrame(columns=["permno", "ticker", "comnam", "exchcd"]),
        yf_name="Nova Robotics Labs",
    )
    assert ing.ingest_fundamentals(990024, "XQRS") == 0


def test_partial_compustat_pull_does_not_delete_stored_quarters(db, monkeypatch):
    """A short snapshot must upsert, never shrink `alphamaxx.db` history."""
    permno = 888773
    db.execute(
        "INSERT OR REPLACE INTO companies (permno,ticker,name) "
        "VALUES (?, 'PART', 'Partial Corp')",
        [permno],
    )
    db.execute("DELETE FROM fundamentals WHERE permno = ?", [permno])
    for fiscal_qtr in ("1987Q4", "1988Q1"):
        db.execute(
            "INSERT INTO fundamentals "
            "(permno,fiscal_qtr,report_date,revenue) VALUES (?,?,?,?)",
            [permno, fiscal_qtr, "1988-04-01", 1.0],
        )

    row = {column: None for column in COMPUSTAT_COLS}
    row.update({
        "gvkey": "999999",
        "datadate": pd.Timestamp("2026-04-30"),
        "fyearq": 2026,
        "fqtr": 1,
        "rdq": pd.Timestamp("2026-06-12"),
        "revtq": 500.0,
    })

    ing = WRDSIngester()
    ing.db_wrds = _StubWRDS(pd.DataFrame([row]))
    monkeypatch.setattr(
        ing, "validate_company_identity",
        lambda p, t: {"permno": p, "ticker": t, "name": "Partial Corp"},
    )
    monkeypatch.setattr(ing, "get_gvkey", lambda p: "999999")
    monkeypatch.setattr(ingestion_module, "refresh_ttm", lambda p: None)

    assert ing.ingest_fundamentals(permno, "PART") == 1

    quarters = {
        fiscal_qtr for (fiscal_qtr,) in db.execute(
            "SELECT fiscal_qtr FROM fundamentals WHERE permno = ?", [permno]
        ).fetchall()
    }
    # The two pre-existing quarters survive alongside the new one.
    assert quarters == {"1987Q4", "1988Q1", "2026Q1"}


def test_fundamentals_ingestion_replaces_complete_snapshot(db, monkeypatch):
    permno = 888772
    db.execute(
        "INSERT OR REPLACE INTO companies (permno,ticker,name) "
        "VALUES (?, 'SNAP', 'Snapshot Corp')",
        [permno],
    )
    db.execute("DELETE FROM fundamentals WHERE permno = ?", [permno])
    for fiscal_qtr in ("1987Q4", "1988Q1"):
        db.execute(
            "INSERT INTO fundamentals "
            "(permno,fiscal_qtr,report_date,revenue) VALUES (?,?,?,?)",
            [permno, fiscal_qtr, "1988-04-01", 1.0],
        )

    row = {column: None for column in COMPUSTAT_COLS}
    row.update({
        "gvkey": "999999",
        "datadate": pd.Timestamp("2026-04-30"),
        "fyearq": 2026,
        "fqtr": 1,
        "rdq": pd.Timestamp("2026-06-12"),
        "revtq": 500.0,
        "cogsq": 120.0,
        "oibdpq": -80.0,
        "oiadpq": -100.0,
        "niq": -90.0,
        "epsfxq": -0.45,
        "oancfy": 50.0,
        "capxy": 10.0,
    })

    # Shrinking the stored history is an identity correction — it requires the
    # explicit --force-replace opt-in.
    ing = WRDSIngester(force_replace=True)
    ing.db_wrds = _StubWRDS(pd.DataFrame([row]))
    monkeypatch.setattr(
        ing,
        "validate_company_identity",
        lambda p, t: {"permno": p, "ticker": t, "name": "Snapshot Corp"},
    )
    monkeypatch.setattr(ing, "get_gvkey", lambda p: "999999")
    monkeypatch.setattr(ingestion_module, "refresh_ttm", lambda p: None)

    assert ing.ingest_fundamentals(permno, "SNAP") == 1
    assert db.execute(
        "SELECT fiscal_qtr, report_date, revenue FROM fundamentals WHERE permno = ?",
        [permno],
    ).fetchall() == [("2026Q1", pd.Timestamp("2026-06-12").date(), 500.0)]
