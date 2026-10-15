"""P16 compute additions: the entry modes' conversions and one holding's valuation (F1)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.compute.portfolio import (
    avg_cost_from_cost_total,
    avg_cost_from_pnl_amount,
    avg_cost_from_pnl_pct,
    position_value,
    units_from_value,
    units_from_value_and_pnl_amount,
)

D = Decimal


def test_avg_cost_from_pnl_pct_gain_loss_and_record() -> None:
    out = avg_cost_from_pnl_pct(D("35.00"), D("0.0769"), inputs=["prov_user", "prov_px"])
    assert out.value["avg_cost"] == D("35.00") / D("1.0769")
    assert out.records[0].calculation == "compute.portfolio.avg_cost_from_pnl_pct@v1"
    assert out.records[0].inputs == ["prov_user", "prov_px"]
    # a loss: price 80 at -20% → bought at 100; break-even → cost = price
    assert avg_cost_from_pnl_pct(D(80), D("-0.2")).value["avg_cost"] == D(100)
    assert avg_cost_from_pnl_pct(D(50), D(0)).value["avg_cost"] == D(50)


@pytest.mark.parametrize(
    ("price", "pnl_pct", "needle"),
    [(D(0), D("0.1"), "ราคา"), (D(-1), D("0.1"), "ราคา"), (D(10), D(-1), "-100%"), (D(10), D(-2), "-100%")],
)
def test_avg_cost_from_pnl_pct_errors(price: Decimal, pnl_pct: Decimal, needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        avg_cost_from_pnl_pct(price, pnl_pct)


def test_avg_cost_from_pnl_amount() -> None:
    # PTT 1,000 shares at 34.50 showing +4,500 THB → cost 30,000 → 30.00 / share
    out = avg_cost_from_pnl_amount(D(1000), D("34.5"), D(4500))
    assert out.value == {"avg_cost": D(30), "cost_basis": D(30000)}
    assert out.records[0].calculation == "compute.portfolio.avg_cost_from_pnl_amount@v1"
    loss = avg_cost_from_pnl_amount(D(10), D(200), D(-500)).value  # -500 USD → cost 2,500
    assert loss == {"avg_cost": D(250), "cost_basis": D(2500)}
    edge = avg_cost_from_pnl_amount(D(10), D(200), D("1999.99")).value  # tiny positive cost is fine
    assert edge["cost_basis"] == D("0.01")


@pytest.mark.parametrize(
    ("qty", "price", "pnl", "needle"),
    [
        (D(0), D(10), D(1), "จำนวน"),
        (D(-5), D(10), D(1), "จำนวน"),
        (D(5), D(0), D(1), "ราคา"),
        (D(10), D(200), D(2000), "≤ 0"),  # gain = whole value → free shares, refused
        (D(10), D(200), D(5000), "≤ 0"),
    ],
)
def test_avg_cost_from_pnl_amount_errors(qty: Decimal, price: Decimal, pnl: Decimal, needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        avg_cost_from_pnl_amount(qty, price, pnl)


def test_units_from_value_and_pnl_amount() -> None:
    # AAPL 2,000 USD showing +400 USD at 200 → 10 units, cost 1,600 → 160 / unit
    out = units_from_value_and_pnl_amount(D(2000), D(400), D(200), inputs=["prov_user"])
    assert out.value == {"units": D(10), "cost_basis": D(1600), "avg_cost": D(160)}
    assert out.records[0].calculation == "compute.portfolio.units_from_value_and_pnl_amount@v1"
    loss = units_from_value_and_pnl_amount(D(1500), D(-500), D(150)).value
    assert loss == {"units": D(10), "cost_basis": D(2000), "avg_cost": D(200)}
    for bad in [(D(0), D(0), D(10)), (D(100), D(0), D(0)), (D(100), D(100), D(10)), (D(100), D(150), D(10))]:
        with pytest.raises(ValueError):
            units_from_value_and_pnl_amount(*bad)


def test_avg_cost_from_cost_total() -> None:
    out = avg_cost_from_cost_total(D(1000), D(30000))
    assert out.value == {"avg_cost": D(30), "cost_basis": D(30000)}
    assert out.records[0].calculation == "compute.portfolio.avg_cost_from_cost_total@v1"
    assert avg_cost_from_cost_total(D(3), D(0)).value["avg_cost"] == 0  # free shares are allowed
    with pytest.raises(ValueError, match="ติดลบ"):
        avg_cost_from_cost_total(D(10), D(-1))
    with pytest.raises(ValueError, match="จำนวน"):
        avg_cost_from_cost_total(D(0), D(100))


def test_units_from_value_without_pnl_leaves_cost_unknown() -> None:
    v = units_from_value(D(2000), None, D(200)).value
    assert v == {"units": D(10), "cost_basis": None, "avg_cost": None}


def test_position_value_full_and_partial() -> None:
    out = position_value(D(1000), D(30), D("34.5"), inputs=["prov_a"])
    assert out.value == {
        "market_value": D(34500),
        "cost_basis": D(30000),
        "unrealized_pnl": D(4500),
        "unrealized_pnl_pct": D("0.15"),
    }
    assert out.records[0].calculation == "compute.portfolio.position_value@v1"
    no_price = position_value(D(10), D(150), None).value
    assert no_price == {
        "market_value": None,
        "cost_basis": D(1500),
        "unrealized_pnl": None,
        "unrealized_pnl_pct": None,
    }
    no_cost = position_value(D(10), None, D(200)).value
    assert no_cost["market_value"] == D(2000) and no_cost["unrealized_pnl"] is None
    free = position_value(D(10), D(0), D(5)).value  # cost 0 → P&L known, % undefined (not ∞)
    assert free["unrealized_pnl"] == D(50) and free["unrealized_pnl_pct"] is None
