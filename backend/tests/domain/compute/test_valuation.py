from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.compute.valuation import DCFAssumptions, dcf, ev_ebitda_value, peer_multiple_stats, wacc


def test_dcf_hand_computed_example() -> None:
    # fcf0=100, 10%% growth for 5 years, wacc=10%%, terminal growth=2%% -> a clean
    # example where every year's PV collapses to fcf0 (110/1.1 = 100, ...).
    assumptions = DCFAssumptions(wacc=0.10, terminal_growth=0.02, years=5, growth_rates=[0.1] * 5)
    result = dcf(Decimal("100"), assumptions, net_debt=Decimal("0"), shares=Decimal("100"))
    assert result.value["pv_by_year"] == pytest.approx([100.0] * 5, abs=1e-6)
    assert result.value["terminal_value_pv"] == pytest.approx(1275.0, abs=1e-4)
    assert result.value["enterprise_value"] == pytest.approx(1775.0, abs=1e-4)
    assert result.value["per_share"] == pytest.approx(17.75, abs=1e-4)


def test_dcf_assumptions_out_of_bounds_wacc_raises() -> None:
    with pytest.raises(ValidationError):
        DCFAssumptions(wacc=0.30, terminal_growth=0.02, years=5, growth_rates=[0.1] * 5)


def test_dcf_assumptions_terminal_growth_must_be_below_wacc() -> None:
    with pytest.raises(ValidationError):
        DCFAssumptions(wacc=0.05, terminal_growth=0.05, years=5, growth_rates=[0.1] * 5)


def test_dcf_assumptions_growth_rate_length_mismatch() -> None:
    with pytest.raises(ValidationError):
        DCFAssumptions(wacc=0.1, terminal_growth=0.02, years=5, growth_rates=[0.1] * 3)


def test_dcf_assumptions_growth_out_of_range() -> None:
    with pytest.raises(ValidationError):
        DCFAssumptions(wacc=0.1, terminal_growth=0.02, years=3, growth_rates=[1.5, 0.1, 0.1])


def test_ev_ebitda_value() -> None:
    result = ev_ebitda_value(Decimal("1000"), 12.0, Decimal("2000"), Decimal("800"))
    assert result.value["enterprise_value"] == pytest.approx(12000.0)
    assert result.value["equity_value"] == pytest.approx(10000.0)
    assert result.value["per_share"] == pytest.approx(12.5)


def test_peer_multiple_stats() -> None:
    result = peer_multiple_stats([10.0, 12.0, 14.0, 16.0])
    assert result.value["median"] == pytest.approx(13.0)
    assert result.value["mean"] == pytest.approx(13.0)


def test_wacc_capm() -> None:
    result = wacc(rf=0.03, beta=1.2, erp=0.05, cost_of_debt=0.06, tax=0.25, e_weight=0.7)
    cost_of_equity = 0.03 + 1.2 * 0.05
    expected = 0.7 * cost_of_equity + 0.3 * 0.06 * 0.75
    assert result.value == pytest.approx(expected)
