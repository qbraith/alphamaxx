"""Pure-Python projections driven entirely by user-supplied assumptions.

Growth, discount, and scenario-spread inputs are decimals: 0.15 means 15%.
The calculations are illustrative scenarios, not probabilities or advice.
"""

from __future__ import annotations

import math


def _finite(name: str, value: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a finite number") from None
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def project_dcf(
    base_value: float,
    growth: float,
    exit_multiple: float,
    discount: float,
    years: int = 5,
) -> dict:
    """Project a per-share metric and discount its terminal value to today."""
    base_value = _finite("base value", base_value)
    growth = _finite("growth", growth)
    exit_multiple = _finite("exit multiple", exit_multiple)
    discount = _finite("discount rate", discount)
    if base_value < 0:
        raise ValueError("base value cannot be negative for terminal-multiple valuation")
    if growth <= -1:
        raise ValueError("growth must be greater than -100%")
    if discount <= -1:
        raise ValueError("discount rate must be greater than -100%")
    if exit_multiple < 0:
        raise ValueError("exit multiple cannot be negative")
    if type(years) is not int or not 1 <= years <= 50:
        raise ValueError("years must be an integer from 1 through 50")

    try:
        path = [round(base_value * (1 + growth) ** year, 2)
                for year in range(years + 1)]
        terminal_value = round(path[-1] * exit_multiple, 2)
        discounted_value = round(terminal_value / (1 + discount) ** years, 2)
    except (OverflowError, ZeroDivisionError):
        raise ValueError("assumptions produce a value outside the supported range") from None
    if not all(
        math.isfinite(value)
        for value in (*path, terminal_value, discounted_value)
    ):
        raise ValueError("assumptions produce a value outside the supported range")

    start = base_value * exit_multiple
    projected_metric_cagr = None
    if start > 0 and terminal_value > 0:
        try:
            projected_metric_cagr = round(
                ((terminal_value / start) ** (1 / years) - 1) * 100,
                1,
            )
        except OverflowError:
            raise ValueError(
                "assumptions produce a value outside the supported range"
            ) from None
        if not math.isfinite(projected_metric_cagr):
            raise ValueError("assumptions produce a value outside the supported range")

    return {
        "path": path,
        "terminal_value": terminal_value,
        "discounted_terminal_value": discounted_value,
        "projected_metric_cagr": projected_metric_cagr,
    }


def scenario_cones(
    base_value: float,
    growth: float,
    exit_multiple: float,
    discount: float,
    years: int = 5,
    spread: float = 0.4,
    multiple_spread: float = 0.15,
) -> dict:
    """Return lower/base/upper paths using explicit user-controlled spreads."""
    growth = _finite("growth", growth)
    spread = _finite("growth spread", spread)
    multiple_spread = _finite("multiple spread", multiple_spread)
    if not 0 <= spread <= 5:
        raise ValueError("growth spread must be between 0% and 500%")
    if not 0 <= multiple_spread < 1:
        raise ValueError("multiple spread must be between 0% and 100%")

    delta = abs(growth) * spread
    upper_growth = growth + delta
    lower_growth = growth - delta
    # project_dcf enforces a growth floor above -100%. Give a useful message
    # when a wide lower scenario crosses that mathematical boundary.
    if lower_growth <= -1:
        raise ValueError("lower-scenario growth must remain greater than -100%")

    base = project_dcf(base_value, growth, exit_multiple, discount, years)
    upper = project_dcf(
        base_value, upper_growth, exit_multiple * (1 + multiple_spread),
        discount, years,
    )
    lower = project_dcf(
        base_value, lower_growth, exit_multiple * (1 - multiple_spread),
        discount, years,
    )
    return {"upper": upper, "base": base, "lower": lower, "years": years}
