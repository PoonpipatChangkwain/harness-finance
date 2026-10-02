from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.domain.compute.portfolio import (
    concentration,
    declared_weights_only,
    market_values,
    rebalance_trades,
    unrealized_pnl,
    weights,
)
from app.domain.models.portfolio import Position
from app.domain.models.symbol import normalize_symbol


def _pos(symbol: str, qty: str, avg_cost: str | None = None, declared: str | None = None) -> Position:
    return Position(
        symbol=normalize_symbol(symbol),
        quantity=Decimal(qty),
        avg_cost=Decimal(avg_cost) if avg_cost else None,
        declared_weight=Decimal(declared) if declared else None,
    )


def test_market_values_and_weights_sum_to_one() -> None:
    positions = [_pos("PTT", "1000"), _pos("AAPL", "10")]
    prices = {"PTT.BK": Decimal("35.5"), "AAPL": Decimal("230.0")}
    fx = {"THB": Decimal("1"), "USD": Decimal("36.0")}
    mv = market_values(positions, prices, fx, "THB")
    assert mv.value["PTT.BK"] == Decimal("35500.0")
    assert mv.value["AAPL"] == Decimal("230.0") * Decimal("36.0") * 10

    w = weights(mv.value, cash=Decimal("10000"))
    total = sum(w.value.values())
    assert abs(total - 1) < Decimal("1e-9")
    assert "CASH" in w.value


def test_unrealized_pnl_none_without_avg_cost() -> None:
    pos = _pos("AAPL", "10")
    result = unrealized_pnl(pos, Decimal("200"))
    assert result.value is None


def test_unrealized_pnl_computed() -> None:
    pos = _pos("AAPL", "10", avg_cost="100")
    abs_pnl, pct_pnl = unrealized_pnl(pos, Decimal("150")).value
    assert abs_pnl == Decimal("500")
    assert pct_pnl == Decimal("0.5")


def test_concentration_single_position_is_max() -> None:
    result = concentration({"AAPL": Decimal("1.0"), "CASH": Decimal("0.0")})
    assert result.value["hhi"] == 1.0
    assert result.value["effective_n"] == 1.0


def test_concentration_diversified() -> None:
    result = concentration(
        {"A": Decimal("0.25"), "B": Decimal("0.25"), "C": Decimal("0.25"), "D": Decimal("0.25")}
    )
    assert abs(result.value["hhi"] - 0.25) < 1e-9
    assert abs(result.value["effective_n"] - 4.0) < 1e-9


def test_rebalance_respects_lot_size_and_no_negative_cash() -> None:
    current_w = {"PTT.BK": Decimal("0.2")}
    target_w = {"PTT.BK": Decimal("0.5")}
    total_value = Decimal("100000")
    prices = {"PTT.BK": Decimal("35.0")}
    lot_size = {"PTT.BK": 100}
    result = rebalance_trades(current_w, target_w, total_value, prices, lot_size)
    leg = result.value["legs"][0]
    assert leg["action"] == "buy"
    assert leg["shares"] % 100 == 0
    assert Decimal(leg["value"]) <= (target_w["PTT.BK"] - current_w["PTT.BK"]) * total_value


def test_declared_weights_only() -> None:
    positions = [_pos("PTT", "0", declared="0.6"), _pos("AAPL", "0", declared="0.4")]
    assert declared_weights_only(positions) is True
    positions_with_qty = [_pos("PTT", "100", declared="0.6")]
    assert declared_weights_only(positions_with_qty) is False


@given(
    values=st.lists(
        st.decimals(min_value="0", max_value="1e9", places=2, allow_nan=False), min_size=1, max_size=6
    ),
    cash=st.decimals(min_value="0", max_value="1e9", places=2, allow_nan=False),
)
def test_weights_sum_to_one_property(values: list[Decimal], cash: Decimal) -> None:
    values_map = {f"S{i}": v for i, v in enumerate(values)}
    if sum(values_map.values()) + cash == 0:
        return
    result = weights(values_map, cash)
    total = sum(result.value.values())
    assert abs(total - 1) < Decimal("1e-6")


@given(
    current=st.decimals(min_value="0", max_value="1", places=4, allow_nan=False),
    target=st.decimals(min_value="0", max_value="1", places=4, allow_nan=False),
    total_value=st.decimals(min_value="1000", max_value="1000000", places=2, allow_nan=False),
)
def test_rebalance_never_overspends_property(current: Decimal, target: Decimal, total_value: Decimal) -> None:
    result = rebalance_trades(
        {"PTT.BK": current}, {"PTT.BK": target}, total_value, {"PTT.BK": Decimal("35")}, {"PTT.BK": 100}
    )
    leg = result.value["legs"][0]
    intended = (target - current) * total_value
    if intended >= 0:
        assert Decimal(leg["value"]) <= intended + Decimal("1e-6")
    else:
        assert Decimal(leg["value"]) >= intended - Decimal("1e-6")
