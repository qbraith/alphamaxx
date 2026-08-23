"""DCF projection and scenario-cone math."""

import pytest

from alphamaxx.services.dcf import project_dcf, scenario_cones


def test_project_dcf_path_and_terminal():
    r = project_dcf(base_value=10.0, growth=0.10, exit_multiple=20, discount=0.10, years=5)
    assert r["path"][0] == 10.0
    assert r["path"][5] == pytest.approx(16.11, abs=0.01)
    assert r["terminal_value"] == pytest.approx(16.11 * 20, abs=0.5)
    assert r["discounted_terminal_value"] == pytest.approx(
        r["terminal_value"] / 1.1 ** 5, abs=0.5
    )


def test_project_dcf_projected_metric_cagr():
    r = project_dcf(10.0, 0.10, 20, 0.10, years=5)
    # Start = 10 × 20 = 200; CAGR from 200 to terminal over 5 years ≈ growth rate
    assert r["projected_metric_cagr"] == pytest.approx(10.0, abs=0.2)


def test_project_dcf_zero_base():
    r = project_dcf(0.0, 0.15, 20, 0.10)
    assert r["terminal_value"] == 0
    assert r["projected_metric_cagr"] is None


def test_scenario_cones_ordering():
    cones = scenario_cones(10.0, 0.10, 20, 0.10, years=5, spread=0.4)
    assert cones["upper"]["terminal_value"] > cones["base"]["terminal_value"]
    assert cones["base"]["terminal_value"] > cones["lower"]["terminal_value"]
    assert cones["years"] == 5
    assert cones["upper"]["path"][1] == pytest.approx(10.0 * 1.14, abs=0.01)


def test_negative_growth_scenario_ordering_is_not_inverted():
    cones = scenario_cones(10.0, -0.10, 20, 0.10, years=5, spread=0.4)
    assert cones["upper"]["terminal_value"] > cones["base"]["terminal_value"]
    assert cones["base"]["terminal_value"] > cones["lower"]["terminal_value"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"discount": -1.0},
        {"growth": -1.0},
        {"exit_multiple": -1.0},
        {"base_value": -1.0},
        {"base_value": float("nan")},
        {"base_value": float("inf")},
    ],
)
def test_project_dcf_rejects_invalid_domains(kwargs):
    values = {
        "base_value": 10.0,
        "growth": 0.10,
        "exit_multiple": 20.0,
        "discount": 0.10,
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        project_dcf(**values)


def test_project_dcf_rejects_boolean_or_extreme_years():
    with pytest.raises(ValueError):
        project_dcf(10.0, 0.10, 20.0, 0.10, years=True)
    with pytest.raises(ValueError):
        project_dcf(10.0, 0.10, 20.0, 0.10, years=51)
