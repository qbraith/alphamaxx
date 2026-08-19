"""Formatting helper edge cases."""

import math

from alphamaxx.web.components import (
    color_cls, fmt_b, fmt_num, fmt_pct, fmt_price, fmt_ratio,
)


def test_fmt_b_boundaries():
    assert fmt_b(None) == "—"
    assert fmt_b(999) == "$999M"
    assert fmt_b(1000) == "$1.0B"
    assert fmt_b(999999) == "$1000.0B"
    assert fmt_b(1234567) == "$1.23T"
    assert fmt_b(-2500) == "$-2.5B"
    assert fmt_b(-1234567) == "$-1.23T"


def test_fmt_pct_sign():
    assert fmt_pct(None) == "—"
    assert fmt_pct(5.06) == "+5.1%"
    assert fmt_pct(-2.34) == "-2.3%"
    assert fmt_pct(0) == "0.0%"


def test_fmt_price_and_ratio():
    assert fmt_price(None) == "—"
    assert fmt_price(1234.5) == "$1,234.50"
    assert fmt_ratio(None) == "—"
    assert fmt_ratio(15.25) == "15.2x"


def test_fmt_num_decimals():
    assert fmt_num(None) == "—"
    assert fmt_num(1234.567, 2) == "1,234.57"
    assert fmt_num(42, 0) == "42"


def test_color_cls():
    assert color_cls(None) == ""
    assert color_cls(0.1) == "positive"
    assert color_cls(0) == "positive"
    assert color_cls(-0.1) == "negative"


def test_nan_and_inf_guarded():
    for f in (fmt_b, fmt_pct, fmt_price, fmt_ratio, fmt_num):
        assert f(float("nan")) == "—"
        assert f(float("inf")) == "—"
        assert f(float("-inf")) == "—"
    assert color_cls(float("nan")) == ""
    assert color_cls(math.inf) == ""
