"""Durable valuation_cache round-trip on a temp DB, including the additive
forward_pe column."""

from alphamaxx.data import get_valuation_cache, upsert_valuation_cache


def test_upsert_and_get_roundtrip_with_forward_pe(db):
    db.execute("DELETE FROM valuation_cache WHERE ticker = 'ZZTEST'")
    upsert_valuation_cache("ZZTEST", 22.5, 1.75, 18.3)
    row = get_valuation_cache(["ZZTEST"])["ZZTEST"]
    assert row["pe"] == 22.5
    assert row["peg"] == 1.75
    assert row["forward_pe"] == 18.3
    assert row["age_s"] >= 0


def test_forward_pe_none_roundtrips_as_none(db):
    db.execute("DELETE FROM valuation_cache WHERE ticker = 'ZZNONE'")
    upsert_valuation_cache("ZZNONE", 10.0, None, None)
    row = get_valuation_cache(["ZZNONE"])["ZZNONE"]
    assert row["pe"] == 10.0
    assert row["forward_pe"] is None


def test_valuation_cache_has_forward_pe_column(db):
    cols = {r[1].lower() for r in db.execute("PRAGMA table_info('valuation_cache')").fetchall()}
    assert "forward_pe" in cols


def test_get_valuation_cache_empty_tickers_returns_empty_dict():
    assert get_valuation_cache([]) == {}
