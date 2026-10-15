"""``size_trade``: every decision number is computed, never taken from an LLM (F1)."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.tools.trading import SizeTradeArgs, size_trade


async def _size(**kw: Any) -> Any:
    base = {"symbol": "PTT.BK", "side": "long", "entry": 34.5, "atr": 0.6, "atr_multiple": 2.0,
            "targets": [37.0], "equity": 1_000_000, "lot_size": 100, "currency": "THB"}  # fmt: skip
    return await size_trade(SizeTradeArgs(**{**base, **kw}), None)  # type: ignore[arg-type]


async def test_long_atr_stop_units_and_rr() -> None:
    out = await _size()
    d = out.data
    assert d["stop"] == pytest.approx(33.3)
    assert d["units"] == 8300  # 10,000 risk / 1.2 = 8333 → board lots of 100
    assert d["risk_reward"][0] == pytest.approx((37.0 - 34.5) / 1.2)
    assert d["risk_fraction"] <= 0.01
    assert all(r.kind == "compute" for r in out.records)


async def test_weight_cap_limits_units() -> None:
    d = (await _size(max_position_weight=0.1)).data
    assert d["position_weight"] <= 0.1 and any("capped" in n for n in d["notes"])


async def test_short_and_wrong_side_level() -> None:
    d = (await _size(side="short", targets=[30.0])).data
    assert d["stop"] == pytest.approx(35.7) and d["units"] > 0
    bad = await _size(stop_basis="level", stop_level=40.0)
    assert bad.data["stop"] is None and bad.data["units"] == 0 and bad.status == "partial"


async def test_fractional_crypto_and_flat() -> None:
    d = (await _size(symbol="BTC-USD", entry=60_000, atr=1_500, lot_size=0.0001, targets=[66_000])).data
    assert d["units"] == pytest.approx(3.3333) and d["stop"] == pytest.approx(57_000)  # 10k / 3k, 0.0001 lots
    flat = (await _size(side="flat")).data
    assert flat["units"] == 0 and flat["stop"] is None


async def test_size_fraction_halves_risk() -> None:
    full, half = (await _size()).data, (await _size(size_fraction=0.5)).data
    assert half["units"] == pytest.approx(full["units"] / 2, abs=100)


# -- pending orders (limit / stop) ----------------------------------------------------------------

XAU = {"symbol": "XAUUSD=X", "side": "short", "entry": 4178.51, "atr": 8.0, "stop_basis": "level",
       "stop_level": 4198.0, "targets": [4163.0], "lot_size": 0.01, "currency": "USD"}  # fmt: skip


async def test_market_short_from_the_screenshot_has_poor_rr() -> None:
    d = (await _size(**XAU)).data
    assert d["order"] == "market" and d["order_error"] is None
    assert d["entry"] == 4178.51 and d["risk_reward"][0] == pytest.approx(15.51 / 19.49, abs=1e-3)


async def test_sell_limit_measures_rr_and_size_from_the_order_price() -> None:
    out = await _size(**XAU, order_type="limit", order_price=4188.0, start="2026-09-30T09:00:00Z",
                      interval="15m", time_stop_bars=16, order_valid_bars=12)  # fmt: skip
    d = out.data
    assert d["order"] == "sell_limit" and d["order_error"] is None
    assert d["entry"] == 4188.0 and d["market_price"] == 4178.51
    assert d["risk_reward"][0] == pytest.approx(2.5)  # (4188-4163) / (4198-4188)
    assert d["risk_amount"] == pytest.approx(10_000, rel=0.01)  # risk from 4188, not 4178.51
    assert d["order_distance_atr"] == pytest.approx(9.49 / 8, abs=1e-3)
    # the time stop counts from the (unknown) fill; the order itself expires after 12 bars
    assert d["time_stop_at"] is None and d["time_stop_from"] == "fill"
    assert d["order_expires_at"].startswith("2026-09-30T12:00")
    assert "sell limit @ 4188" in out.summary
    calcs = {(r.calculation or "").split("@")[0] for r in out.records}
    assert calcs >= {"compute.trade.order", "compute.trade.order_expiry"}


@pytest.mark.parametrize(
    ("side", "order_type", "price", "ok"),
    [
        ("long", "limit", 34.0, True), ("long", "limit", 35.0, False),  # buy limit below
        ("short", "limit", 35.5, True), ("short", "limit", 34.0, False),  # sell limit above
        ("long", "stop", 35.5, True), ("long", "stop", 34.0, False),  # buy stop above
        ("short", "stop", 34.0, True), ("short", "stop", 35.0, False),  # sell stop below
    ],
)  # fmt: skip
async def test_pending_order_must_be_on_the_right_side(
    side: str, order_type: str, price: float, ok: bool
) -> None:
    targets = [40.0] if side == "long" else [28.0]
    d = (await _size(side=side, order_type=order_type, order_price=price, targets=targets)).data
    assert (d["order_error"] is None) is ok
    assert d["order"] == f"{'buy' if side == 'long' else 'sell'}_{order_type}"


async def test_pending_order_too_far_or_unmeasurable_fails_closed() -> None:
    far = (await _size(order_type="limit", order_price=33.0)).data  # 1.5 / 0.6 = 2.5 ATR > 2
    assert "2.50 ATR from the current price" in far["order_error"]
    wider = (await _size(order_type="limit", order_price=33.0, max_order_distance_atr=3.0)).data
    assert wider["order_error"] is None
    no_atr = (
        await _size(order_type="limit", order_price=34.0, atr=None, stop_basis="level", stop_level=33.0)
    ).data
    assert "no ATR" in no_atr["order_error"]
    missing = (await _size(order_type="limit")).data
    assert "order_price is missing" in missing["order_error"] and missing["entry"] == 34.5


async def test_target_r_follows_the_entry_and_stop() -> None:
    d = (await _size(targets=[], target_r=[1.5, 2.5])).data  # stop 33.3, risk 1.2
    assert d["targets"] == pytest.approx([36.3, 37.5])
    assert d["risk_reward"] == pytest.approx([1.5, 2.5])
    s = (await _size(side="short", targets=[], target_r=[1.5])).data  # stop 35.7
    assert s["targets"] == pytest.approx([32.7])


async def test_stop_floor_widens_a_tight_stop_and_shrinks_the_size() -> None:
    # 1 x ATR = 0.6 below -> floored to 2 x ATR = 1.2 (stop 33.3), units sized from the wider stop
    d = (await _size(atr_multiple=1.0, min_stop_atr=2.0)).data
    assert d["stop"] == pytest.approx(33.3) and d["units"] == 8300
    assert any(n.startswith("stop widened from 33.9 to 33.3") for n in d["notes"])
    # a level stop inside the floor is widened too; one beyond it is kept
    d = (await _size(stop_basis="level", stop_level=34.0, min_stop_atr=2.0)).data
    assert d["stop"] == pytest.approx(33.3)
    d = (await _size(stop_basis="level", stop_level=32.5, min_stop_atr=2.0)).data
    assert d["stop"] == 32.5 and not any("widened" in n for n in d["notes"])
    # short mirror
    d = (await _size(side="short", atr_multiple=1.0, min_stop_atr=2.0)).data
    assert d["stop"] == pytest.approx(35.7)


async def test_first_target_is_set_by_the_system_from_the_floored_stop() -> None:
    # stop 33.3 (risk 1.2): T1 = 34.5 + 1.6 x 1.2 = 36.42; the trader's 37.0 becomes target 2
    d = (await _size(first_target_r=1.6)).data
    assert d["targets"] == [pytest.approx(36.42), 37.0]
    assert d["risk_reward"][0] == pytest.approx(1.6)
    # trader targets inside T1 are dropped with a note
    d = (await _size(targets=[35.0, 38.0], first_target_r=1.6)).data
    assert d["targets"] == [pytest.approx(36.42), 38.0]
    assert any("dropped" in n for n in d["notes"])
    # with the floor, T1 is measured from the widened stop
    d = (await _size(atr_multiple=1.0, min_stop_atr=2.0, first_target_r=1.6, targets=[])).data
    assert d["stop"] == pytest.approx(33.3) and d["targets"] == [pytest.approx(36.42)]
    # short
    d = (await _size(side="short", targets=[31.0], first_target_r=1.6)).data
    assert d["targets"] == [pytest.approx(32.58), 31.0]
