"""P12 compute additions: P&L summary, value entry, post-trade weights, cash fitting, risk contribution."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.compute.portfolio import (
    pnl_summary,
    post_trade,
    rebalance_trades,
    trim_buys_to_cash,
    units_from_value,
)
from app.domain.compute.returns import risk_contribution
from app.domain.policy import MaxTurnover, MinCashWeight, NoSellList

D = Decimal


def test_pnl_summary_skips_unknown_cost_and_prev_close() -> None:
    rows = [
        {"value": D(110), "cost": D(100), "prev_value": D(105)},
        {"value": D(50), "cost": None, "prev_value": None},
    ]
    out = pnl_summary(rows)
    v = out.value
    assert v["invested_value"] == D(160)
    assert v["cost_basis"] == D(100) and v["unrealized_pnl"] == D(10)
    assert v["unrealized_pnl_pct"] == D("0.1")
    assert v["day_pnl"] == D(5) and v["day_pnl_pct"] == D(5) / D(105)
    assert out.records[0].calculation == "compute.portfolio.pnl_summary@v1"


def test_pnl_summary_without_any_cost_is_none_not_zero() -> None:
    v = pnl_summary([{"value": D(10), "cost": None, "prev_value": None}]).value
    assert v["unrealized_pnl"] is None and v["cost_basis"] is None and v["day_pnl"] is None


def test_units_from_value_dime_style_entry() -> None:
    # NVDA 549.96 USD, +14.52%, last price 180
    out = units_from_value(D("549.96"), D("0.1452"), D("180"), inputs=["prov_user"])
    v = out.value
    assert v["units"] == D("549.96") / D(180)
    assert v["cost_basis"] == D("549.96") / D("1.1452")
    assert v["avg_cost"] * v["units"] == pytest.approx(v["cost_basis"])
    assert out.records[0].inputs == ["prov_user"]
    with pytest.raises(ValueError):
        units_from_value(D(100), D(-1), D(10))
    with pytest.raises(ValueError):
        units_from_value(D(100), D(0), D(0))


def test_post_trade_weights_cash_and_turnover() -> None:
    values = {"A": D(600), "B": D(300)}
    legs: list[dict[str, object]] = [
        {"symbol": "A", "value": "-200", "shares": -2},
        {"symbol": "C", "value": "150", "shares": 3},
    ]
    v = post_trade(values, D(100), legs).value
    assert v["cash"] == D(150)
    assert v["weights"] == {"A": D("0.4"), "B": D("0.3"), "C": D("0.15"), "CASH": D("0.15")}  # type: ignore[comparison-overlap]
    assert v["turnover"] == D("0.35")


def test_trim_buys_to_cash_never_overspends() -> None:
    prices = {"PTT.BK": D("34.5"), "AAPL": D(7200)}
    lots = {"PTT.BK": 100, "AAPL": 1}
    # sell 1 AAPL (+7,200), buy 300 PTT (-10,350): 3,150 short with no cash
    legs: list[dict[str, object]] = [
        {"symbol": "AAPL", "action": "sell", "shares": -1, "value": "-7200"},
        {"symbol": "PTT.BK", "action": "buy", "shares": 300, "value": "10350"},
    ]
    v = trim_buys_to_cash(legs, prices, lots, D(0)).value
    assert Decimal(str(v["cash_after"])) >= 0
    fitted = {leg["symbol"]: leg for leg in v["legs"]}  # type: ignore[attr-defined]
    assert fitted["PTT.BK"]["shares"] == 200 and v["trimmed"] == {"PTT.BK": 100}


def test_rebalance_then_fit_respects_lots() -> None:
    total = D(156_500)
    current = {"PTT.BK": D(34_500) / total, "AAPL": D(72_000) / total}
    target = {"PTT.BK": D("0.30"), "AAPL": D("0.20")}
    prices = {"PTT.BK": D("34.5"), "AAPL": D(7200)}
    legs = rebalance_trades(current, target, total, prices, {"PTT.BK": 100, "AAPL": 1}).value["legs"]
    by = {leg["symbol"]: leg for leg in legs}  # type: ignore[attr-defined]
    assert by["PTT.BK"]["shares"] % 100 == 0 and by["PTT.BK"]["action"] == "buy"
    assert by["AAPL"]["action"] == "sell"


def test_risk_contribution_sums_to_one_and_ranks_the_volatile_name() -> None:
    calm = [0.001, -0.001] * 50
    wild = [0.03, -0.028] * 50
    rc = risk_contribution({"calm": calm, "wild": wild}, {"calm": 0.5, "wild": 0.5}).value
    assert sum(rc.values()) == pytest.approx(1.0)
    assert rc["wild"] > 0.9
    assert risk_contribution({"a": [0.0] * 10}, {"a": 1.0}).value == {}


def test_new_policy_rules() -> None:
    assert MinCashWeight(0.05).evaluate({"cash_weight": 0.04}, {}).passed is False
    assert MinCashWeight(0.05).evaluate({"cash_weight": 0.05}, {}).passed is True
    assert MinCashWeight(0.05).evaluate({}, {}).passed is False  # unknown fails closed
    hit = NoSellList().evaluate({"sells": ["PTT.BK", "AAPL"]}, {"no_sell": ["ptt.bk"]})
    assert not hit.passed and hit.values["violations"] == ["PTT.BK"]
    assert NoSellList().evaluate({"sells": []}, {"no_sell": ["X"]}).passed
    assert MaxTurnover(0.3).evaluate({"turnover": 0.31}, {}).passed is False
    assert MaxTurnover(0.3).evaluate({"turnover": 0.3}, {}).passed is True
