"""SMA / RSI / pct-change math against known series."""

import pytest

from alphamaxx.data.momentum import _pct_change, _rsi, _sma


def test_sma_basic():
    assert _sma([1, 2, 3, 4, 5], 5) == 3
    assert _sma([1, 2, 3, 4, 5], 3) == 4  # last three: 3,4,5
    assert _sma([1, 2], 5) is None


def test_pct_change():
    assert _pct_change(110, 100) == 10.0
    assert _pct_change(90, 100) == -10.0
    assert _pct_change(100, 0) is None
    assert _pct_change(100, None) is None


def test_rsi_insufficient_data():
    assert _rsi([1.0] * 14, 14) is None  # needs period + 1 points


def test_rsi_all_gains_is_100():
    prices = [float(i) for i in range(1, 31)]
    assert _rsi(prices, 14) == 100.0


def test_rsi_known_value():
    # Wilder's classic worked example (14-period RSI ≈ 70.46 after first window)
    prices = [
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
        45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
    ]
    rsi = _rsi(prices, 14)
    assert rsi == pytest.approx(70.46, abs=0.1)


def test_rsi_range():
    prices = [100 + ((-1) ** i) * (i % 7) for i in range(60)]
    rsi = _rsi([float(p) for p in prices], 14)
    assert 0 <= rsi <= 100


def _seed_weekly_prices(db, permno: int, closes: list[float]) -> None:
    from datetime import date, timedelta
    db.execute("DELETE FROM prices WHERE permno = ?", [permno])
    monday = date(2024, 1, 1)
    for i, px in enumerate(closes):
        db.execute(
            "INSERT OR REPLACE INTO prices (permno, price_date, adj_close) VALUES (?,?,?)",
            [permno, monday + timedelta(weeks=i), px],
        )


def test_refresh_momentum_uses_weekly_approximation_windows(db, synthetic_company):
    """SMA-200d/50d are sampled over 40/10 WEEKLY closes, not 200/50 rows."""
    from alphamaxx.data.momentum import get_company_momentum, refresh_momentum

    closes = [float(i) for i in range(1, 61)]  # 60 weekly bars: 1..60
    _seed_weekly_prices(db, synthetic_company, closes)
    refresh_momentum(synthetic_company)
    m = get_company_momentum(synthetic_company)

    assert m["sma_200"] == pytest.approx(sum(closes[-40:]) / 40)  # 40.5
    assert m["sma_50"] == pytest.approx(sum(closes[-10:]) / 10)   # 55.5
    assert m["pct_from_200"] == pytest.approx((60 / 40.5 - 1) * 100, abs=0.01)


def test_refresh_momentum_short_history(db, synthetic_company):
    """Under 40 weekly bars there is no ~200d SMA, but the ~50d SMA exists."""
    from alphamaxx.data.momentum import get_company_momentum, refresh_momentum

    _seed_weekly_prices(db, synthetic_company, [float(i) for i in range(1, 31)])
    refresh_momentum(synthetic_company)
    m = get_company_momentum(synthetic_company)

    assert m["sma_200"] is None
    assert m["pct_from_200"] is None
    assert m["sma_50"] == pytest.approx(sum(range(21, 31)) / 10)  # 25.5
