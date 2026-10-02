"""Portfolio simulator (Phase 15.2): every number below is worked out by hand in the comment."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import pytest

from app.domain.compute.backtest import (
    SimConfig,
    SimDecision,
    buy_and_hold,
    performance,
    simulate,
    sma_cross,
)
from app.domain.models.market import OHLCVBar

D0 = date(2026, 1, 5)


def day(n: int) -> date:
    return D0 + timedelta(days=n)


def bar(n: int, o: float, h: float, low: float, c: float) -> OHLCVBar:
    ts = datetime.combine(day(n), datetime.min.time(), tzinfo=UTC)
    return OHLCVBar(ts=ts, open=D(str(o)), high=D(str(h)), low=D(str(low)), close=D(str(c)), volume=1)


def flat_bars(start: int, n: int, px: float = 100) -> list[OHLCVBar]:
    return [bar(i, px, px + 1, px - 1, px) for i in range(start, start + n)]


def long(sym: str = "AAA", **kw: Any) -> SimDecision:
    base: dict[str, Any] = {
        "symbol": sym, "currency": "THB", "decided_on": D0, "side": "long",
        "stop": D(90), "targets": [D(120)], "ref": f"run-{sym}",
    }  # fmt: skip
    return SimDecision(**{**base, **kw})


EXACT = SimConfig(slippage_bps=D(0), max_position_weight=None)  # 100k, 1% risk, no costs


def run(
    decisions: list[SimDecision], bars: dict[str, list[OHLCVBar]], cfg: SimConfig = EXACT, **kw: Any
) -> Any:
    return simulate(decisions, bars, cfg, **kw).value


# -- fills and exits ---------------------------------------------------------------------------


def test_target_hit_long() -> None:
    # fill at the next open 100; risk 1% of 100k = 1000 over 10/unit → 100 units; TP 120 → +2000, +2R
    res = run(
        [long()], {"AAA": [bar(0, 99, 100, 98, 99), bar(1, 100, 105, 98, 104), bar(2, 104, 121, 103, 119)]}
    )
    [t] = res.trades
    assert (t.entry_price, t.units, t.exit_price, t.exit_reason) == (D(100), D(100), D(120), "tp")
    assert t.pnl == D(2000) and t.r_multiple == D(2) and t.entry_at.date() == day(1)
    assert res.final_equity == D(102_000)


def test_a_decision_never_fills_on_its_own_days_bar() -> None:
    # decided on the close of D0: the D0 bar is history, entry is D1's open
    res = run([long()], {"AAA": [bar(0, 50, 200, 10, 100), bar(1, 100, 101, 99, 100)]})
    assert res.trades[0].entry_at.date() == day(1)


def test_gap_through_the_stop_exits_at_the_open() -> None:
    # opens at 85 below the 90 stop: exit 85 → -15 x 100 = -1500, -1.5R
    res = run([long()], {"AAA": [bar(1, 100, 101, 99, 100), bar(2, 85, 86, 80, 82)]})
    [t] = res.trades
    assert (t.exit_price, t.exit_reason, t.gap, t.pnl, t.r_multiple) == (
        D(85),
        "sl",
        True,
        D(-1500),
        D("-1.5"),
    )


@pytest.mark.parametrize(
    ("rule", "exit_px", "reason"), [("stop_first", 90, "sl"), ("target_first", 120, "tp")]
)
def test_stop_and_target_in_one_bar(rule: str, exit_px: int, reason: str) -> None:
    cfg = EXACT.model_copy(update={"intrabar_rule": rule})
    res = run([long()], {"AAA": [bar(1, 100, 101, 99, 100), bar(2, 100, 125, 88, 100)]}, cfg)
    assert (res.trades[0].exit_price, res.trades[0].exit_reason) == (D(exit_px), reason)


def test_market_entry_is_skipped_when_the_open_gaps_past_the_level() -> None:
    res = run([long(), long("BBB")], {"AAA": [bar(1, 89, 95, 85, 90)], "BBB": [bar(1, 121, 125, 119, 122)]})
    assert res.trades == [] and [s.reason for s in res.skipped] == ["gap_invalidated"] * 2


def test_time_stop_closes_at_the_close_after_n_bars() -> None:
    bars = [
        bar(1, 100, 101, 99, 100),
        bar(2, 100, 102, 99, 101),
        bar(3, 101, 104, 100, 103),
        *flat_bars(4, 2),
    ]
    res = run([long(time_stop_bars=2)], {"AAA": bars})
    [t] = res.trades
    assert (t.exit_reason, t.exit_price, t.bars_held, t.exit_at.date()) == ("time_stop", D(103), 2, day(3))


def test_open_position_is_closed_at_the_end_of_the_test() -> None:
    res = run([long()], {"AAA": [bar(1, 100, 101, 99, 100), bar(2, 100, 106, 99, 105)]})
    assert (res.trades[0].exit_reason, res.trades[0].pnl) == ("end_of_test", D(500))


def test_equal_split_scales_out_per_target() -> None:
    # 100 units, targets 110/120: 50 @110 (+500) then 50 @120 (+1000); avg exit 115 → 1.5R
    cfg = EXACT.model_copy(update={"scale_out": "equal_split"})
    d = long(targets=[D(110), D(120)])
    res = run(
        [d], {"AAA": [bar(1, 100, 101, 99, 100), bar(2, 100, 112, 99, 111), bar(3, 111, 121, 110, 118)]}, cfg
    )
    [t] = res.trades
    assert (t.exit_price, t.pnl, t.r_multiple, t.exit_at.date()) == (D(115), D(1500), D("1.5"), day(3))


# -- pending orders ----------------------------------------------------------------------------


def test_limit_order_fills_when_touched_and_expires_otherwise() -> None:
    d = long(order_type="limit", order_price=D(95), order_valid_bars=2)
    # D1 low 96: not yet; D2 opens 97, trades down to 94 → filled at the limit 95
    filled = run([d], {"AAA": [bar(1, 100, 101, 96, 99), bar(2, 97, 98, 94, 96), *flat_bars(3, 1, 96)]})
    assert filled.trades[0].entry_price == D(95) and filled.trades[0].units == D(200)  # 1000 / (95 - 90)

    never = run([d], {"AAA": [bar(1, 100, 101, 96, 99), bar(2, 99, 100, 96, 98), *flat_bars(3, 1)]})
    assert never.trades == [] and [s.reason for s in never.skipped] == ["order_expired"]


def test_stop_entry_fills_on_the_breakout_with_slippage() -> None:
    # buy stop 105: D2 opens 104, trades to 107 → fill 105 + 10 bps = 105.105; the fill bar checks
    # only the stop, so its 107 high does not count against the 106 target until the next bar
    cfg = EXACT.model_copy(update={"slippage_bps": D(10)})
    d = long(order_type="stop", order_price=D(105), targets=[D(106)])
    res = run(
        [d], {"AAA": [bar(1, 100, 104, 99, 103), bar(2, 104, 107, 103, 106), bar(3, 106, 107, 105, 106)]}, cfg
    )
    [t] = res.trades
    assert t.entry_price == D("105.105") and t.entry_at.date() == day(2)
    assert (t.exit_reason, t.exit_at.date()) == ("tp", day(3))


def test_decisions_that_cannot_trade_are_recorded_with_a_reason() -> None:
    bars = {"AAA": [bar(1, 100, 101, 99, 100), *flat_bars(2, 3)]}
    res = run(
        [
            long(side="flat"),
            long(stop=None),
            long(order_type="limit", order_price=None),
            long(order_type="limit", order_price=D(89)),  # limit below its own stop
            long(),  # opens
            long(decided_on=day(1)),  # same side again
        ],
        bars,
    )
    assert [s.reason for s in res.skipped] == [
        "flat", "invalid_levels", "invalid_levels", "invalid_levels", "already_open",
    ]  # fmt: skip
    assert len(res.trades) == 1


def test_a_newer_decision_replaces_a_resting_order() -> None:
    first = long(order_type="limit", order_price=D(95))
    second = long(decided_on=day(1))  # market, next day
    res = run(
        [first, second], {"AAA": [bar(1, 100, 101, 96, 100), bar(2, 100, 101, 99, 100), *flat_bars(3, 1)]}
    )
    assert [(s.reason, s.detail) for s in res.skipped] == [("order_expired", "replaced by a newer decision")]
    assert res.trades[0].entry_at.date() == day(2)


def test_target_first_takes_the_target_then_stops_the_rest() -> None:
    cfg = EXACT.model_copy(update={"intrabar_rule": "target_first", "scale_out": "equal_split"})
    d = long(targets=[D(110), D(130)])
    res = run([d], {"AAA": [bar(1, 100, 101, 99, 100), bar(2, 100, 111, 89, 95)]}, cfg)
    [t] = res.trades
    # 50 @110 (+500), 50 @90 (-500) → avg exit 100, 0R
    assert (t.exit_reason, t.exit_price, t.pnl, t.r_multiple) == ("sl", D(100), D(0), D(0))


def test_limit_that_gaps_below_the_stop_is_not_filled_into_a_loss() -> None:
    d = long(order_type="limit", order_price=D(95))
    res = run([d], {"AAA": [bar(1, 85, 88, 84, 86)]})
    assert res.trades == [] and res.skipped[0].reason == "gap_invalidated"


# -- sizing: why the starting capital matters -------------------------------------------------


@pytest.mark.parametrize(
    ("capital", "units", "skip"),
    [(10_000, None, "below_one_lot"), (100_000, D(500), None), (1_000_000, D(5800), None)],
)
def test_thai_lot_and_weight_cap(capital: int, units: D | None, skip: str | None) -> None:
    # 34 THB, stop 1 THB away, risk 1%, max weight 20%, lot 100 (the table in phase-15-backtest.md)
    cfg = SimConfig(initial_capital=D(capital), slippage_bps=D(0))
    d = long(stop=D(33), targets=[D(37)], lot_size=D(100))
    res = run([d], {"AAA": [bar(1, 34, 34.5, 33.5, 34)]}, cfg)
    if skip:
        assert res.trades == [] and res.skipped[0].reason == skip
    else:
        assert res.trades[0].units == units


def test_no_buying_power_and_max_positions() -> None:
    # 100% weight cap with a tight stop: risk 5000 / 1 = 5000 units, capped at 100k / 100 = 1000 —
    # the first trade takes all the cash (bars stay above the 99 stop)
    cfg = EXACT.model_copy(update={"max_position_weight": D(1), "risk_pct": D("0.05")})
    tight = {"stop": D(99), "targets": [D(130)]}
    quiet = [bar(1, 100, 101, 99.5, 100), bar(2, 100, 101, 99.5, 100)]
    res = run([long(**tight), long("BBB", **tight)], {"AAA": quiet, "BBB": quiet}, cfg)
    assert res.trades[0].units == D(1000) and [s.reason for s in res.skipped] == ["no_buying_power"]

    one = EXACT.model_copy(update={"max_open_positions": 1})
    res = run([long(), long("BBB")], {"AAA": quiet, "BBB": quiet}, one)
    assert [s.reason for s in res.skipped] == ["max_positions"]


def test_compounding_sizes_from_current_equity() -> None:
    # trade 1: +2000 → equity 102k; trade 2 risks 1% of 102k = 1020 → 102 units (no compounding: 100)
    first = long()
    second = long(decided_on=day(2))
    bars = [bar(1, 100, 105, 98, 104), bar(2, 104, 121, 103, 100), bar(3, 100, 101, 99, 100)]
    assert run([first, second], {"AAA": bars}).trades[1].units == D(102)
    flat_cfg = EXACT.model_copy(update={"compounding": False})
    assert run([first, second], {"AAA": bars}, flat_cfg).trades[1].units == D(100)


# -- shorts, reversal, FX ----------------------------------------------------------------------


def test_short_stock_not_allowed_but_the_opposite_view_closes_the_long() -> None:
    sell = SimDecision(
        symbol="AAA", currency="THB", decided_on=day(1), side="short", stop=D(110), targets=[D(80)]
    )
    res = run([long(), sell], {"AAA": [bar(1, 100, 101, 99, 100), bar(2, 103, 104, 102, 103)]})
    [t] = res.trades
    assert (t.exit_reason, t.exit_price, t.pnl) == ("reverse", D(103), D(300))
    assert [s.reason for s in res.skipped] == ["short_not_allowed"]


def test_forex_short_in_a_thb_account() -> None:
    # 1% of 100k THB = 1000 THB; risk/unit = 0.01 USD x 35 = 0.35 THB → 2857 → lot 1000 → 2000 units
    # TP 1.08: 0.02 x 2000 x 35 = +1400 THB
    d = SimDecision(
        symbol="EURUSD=X", currency="USD", asset_type="forex", decided_on=D0, side="short",
        stop=D("1.11"), targets=[D("1.08")], lot_size=D(1000),
    )  # fmt: skip
    bars = {"EURUSD=X": [bar(1, 1.10, 1.105, 1.095, 1.10), bar(2, 1.10, 1.101, 1.079, 1.085)]}
    res = run([d], bars, fx={"USD": [(D0, D(35))]})
    [t] = res.trades
    assert (t.units, t.exit_reason, t.pnl) == (D(2000), "tp", D(1400))
    with pytest.raises(ValueError, match="no FX series"):
        run([d], bars)


# -- costs -------------------------------------------------------------------------------------


def test_commission_and_slippage() -> None:
    # 10 bps slippage: buy at 100.1, stop 90 → 1000 / 10.1 = 99.0099 → 99 units
    # TP 120 is a resting limit (no slippage): gross (120 - 100.1) x 99 = 1970.1
    # commission 0.1%: in 99 x 100.1 x 0.001 = 9.9099, out 99 x 120 x 0.001 = 11.88 → net 1948.3101
    cfg = SimConfig(slippage_bps=D(10), commission_pct=D("0.001"), max_position_weight=None)
    res = run([long()], {"AAA": [bar(1, 100, 101, 99, 100), bar(2, 104, 121, 103, 119)]}, cfg)
    [t] = res.trades
    assert t.entry_price == D("100.1") and t.units == D(99)
    assert t.fees == D("21.7899") and t.pnl == D("1948.3101")
    assert res.final_equity == D(100_000) + t.pnl


# -- bookkeeping, metrics, benchmark -----------------------------------------------------------


def test_no_bars_after_the_decision() -> None:
    res = run([long(decided_on=day(9))], {"AAA": flat_bars(1, 3)})
    assert [s.reason for s in res.skipped] == ["no_bars"] and res.final_equity == D(100_000)


def test_equity_curve_marks_to_market_and_is_deterministic() -> None:
    bars = {"AAA": [bar(1, 100, 101, 99, 100), bar(2, 100, 106, 99, 95), bar(3, 95, 121, 94, 110)]}
    a = simulate([long()], bars, EXACT)
    b = simulate([long()], bars, EXACT)
    assert a.value.model_dump() == b.value.model_dump()
    curve = a.value.equity_curve
    assert [p.equity for p in curve] == [D(100_000), D(99_500), D(102_000)]  # -5 x 100, then TP
    assert curve[1].open_positions == 1 and curve[1].gross_exposure == D(9500) / D(99_500)
    assert a.records[0].calculation == "compute.backtest.simulate@v1"


def test_performance_and_buy_and_hold() -> None:
    bars = {"AAA": [bar(1, 100, 101, 99, 100), bar(2, 100, 106, 99, 95), bar(3, 95, 121, 94, 110)]}
    sim = simulate([long()], bars, EXACT).value
    bench = buy_and_hold(bars, {"AAA": "THB"}, EXACT).value
    assert bench[0] == (day(1), D(100_000)) and bench[-1] == (day(3), D(110_000))  # 1000 units 100 → 110
    perf = performance(sim, benchmark=bench).value
    assert perf["total_return"] == pytest.approx(0.02) and perf["benchmark_return"] == pytest.approx(0.10)
    assert perf["alpha"] == pytest.approx(-0.08)
    assert perf["max_drawdown"] == pytest.approx(-0.005)
    assert (perf["trades"], perf["win_rate"], perf["expectancy_r"]) == (1, 1.0, 2.0)
    assert perf["final_equity"] == D("102000.00") and perf["exits"] == {"tp": 1}


def test_sma_cross_baseline_runs_through_the_same_simulator() -> None:
    # 60 bars down then 60 up: one golden cross on the way up
    closes = [200 - i for i in range(60)] + [140 + 2 * i for i in range(60)]
    bars = [bar(i, c, c + 1, c - 1, c) for i, c in enumerate(closes)]
    decisions = sma_cross("AAA", bars, currency="THB")
    assert [d.side for d in decisions] == ["long"]
    d = decisions[0]
    assert d.stop is not None and d.stop < bars[0].close and d.targets[0] > d.stop
    res = run(decisions, {"AAA": bars})
    assert len(res.trades) == 1 and res.trades[0].ref == "baseline:sma20/50"
