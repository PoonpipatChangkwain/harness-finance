from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.compute.returns import (
    annualized_volatility,
    beta,
    cumulative_return,
    log_returns,
    max_drawdown,
    simple_returns,
)


def test_simple_returns() -> None:
    result = simple_returns([Decimal("100"), Decimal("110"), Decimal("99")])
    assert result.value == pytest.approx([0.10, -0.10])


def test_log_returns_matches_simple_for_small_moves() -> None:
    result = log_returns([100.0, 101.0])
    assert result.value[0] == pytest.approx(0.00995033, abs=1e-6)


def test_cumulative_return() -> None:
    result = cumulative_return([100.0, 150.0])
    assert result.value == pytest.approx(0.5)


def test_max_drawdown_known_series() -> None:
    closes = [100, 120, 90, 95, 130, 80]
    result = max_drawdown(closes)
    assert result.value["mdd"] == pytest.approx(-0.38461538, abs=1e-6)
    assert result.value["peak_index"] == 4
    assert result.value["trough_index"] == 5


def test_max_drawdown_monotonic_up_is_zero() -> None:
    result = max_drawdown([100, 110, 120, 130])
    assert result.value["mdd"] == 0.0


def test_annualized_volatility_zero_for_constant_returns() -> None:
    result = annualized_volatility([0.0, 0.0, 0.0, 0.0])
    assert result.value == 0.0


def test_beta_of_series_with_itself_is_one() -> None:
    rets = [0.01, -0.02, 0.03, 0.015, -0.01]
    result = beta(rets, rets)
    assert result.value == pytest.approx(1.0, abs=1e-9)
