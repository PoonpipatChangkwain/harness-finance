from __future__ import annotations

from decimal import Decimal

from app.domain.compute.risk import forex_pip_value, position_size, risk_reward, stop_from_atr


def test_position_size_hand_example() -> None:
    result = position_size(
        equity=Decimal("50000"),
        risk_pct=Decimal("0.02"),
        entry=Decimal("50"),
        stop=Decimal("48"),
        lot_size=100,
    )
    assert result.value["units"] == 500
    assert result.value["risk_amount"] == "1000"


def test_position_size_zero_stop_distance() -> None:
    result = position_size(Decimal("50000"), Decimal("0.02"), Decimal("50"), Decimal("50"))
    assert result.value["units"] == 0


def test_stop_from_atr_long_and_short() -> None:
    long_stop = stop_from_atr(Decimal("100"), Decimal("2"), Decimal("1.5"), "long")
    short_stop = stop_from_atr(Decimal("100"), Decimal("2"), Decimal("1.5"), "short")
    assert long_stop.value == Decimal("97")
    assert short_stop.value == Decimal("103")


def test_risk_reward_long() -> None:
    result = risk_reward(Decimal("100"), Decimal("95"), Decimal("115"), "long")
    assert result.value == Decimal("3")


def test_risk_reward_zero_risk_is_none() -> None:
    result = risk_reward(Decimal("100"), Decimal("100"), Decimal("115"), "long")
    assert result.value is None


def test_forex_pip_value_standard_lot_eurusd() -> None:
    result = forex_pip_value("EURUSD", Decimal("100000"), "USD", Decimal("1"))
    assert result.value == Decimal("10.0000")


def test_forex_pip_value_jpy_pair_uses_bigger_pip() -> None:
    result = forex_pip_value("USDJPY", Decimal("100000"), "USD", Decimal("1"))
    assert result.value == Decimal("1000.00")
