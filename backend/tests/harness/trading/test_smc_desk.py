"""LLM-only desk: original trader v6 by default; v8 + the opt-in Smart Money Concepts block."""

from __future__ import annotations

from typing import Any

from app.domain.policy import SmcStructureAlignment
from app.domain.workflows.trading import (
    TRADER_LLM_ONLY_VERSION,
    TRADER_SMC_VERSION,
    _smc_text,
    _system,
    _trader_version,
)

ML_LONG = {"available": True, "signal": "long"}

SMC: dict[str, Any] = {
    "available": True,
    "interval": "15m",
    "bar_ts": "2026-09-01T10:00:00+00:00",
    "swing": {
        "trend": "bullish",
        "breaks": [{"tag": "CHoCH", "bias": "bullish", "level": 2500.0, "bars_ago": 7}],
    },
    "internal": {"trend": "bearish", "breaks": []},
    "premium_discount": {
        "top": 2520.0,
        "bottom": 2480.0,
        "equilibrium": 2500.0,
        "position": 0.3,
        "zone": "discount",
    },
    "strong_weak": {"high": {"strength": "weak"}, "low": {"strength": "strong"}},
    "order_blocks": {
        "internal": [
            {"bias": "bullish", "low": 2488.0, "high": 2490.0, "bars_ago": 12, "distance_atr": -1.2}
        ],
        "swing": [],
    },
    "fair_value_gaps": [
        {"bias": "bullish", "low": 2491.0, "high": 2492.5, "bars_ago": 3, "distance_atr": 0.0}
    ],
    "equal_levels": [{"type": "EQH", "level": 2519.8, "bars_ago": 20}],
    "higher_tf": {"swing": {"trend": "bullish", "breaks": []}, "premium_discount": None, "strong_weak": None},
}


def _state(ml: dict[str, Any], smc: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"input": {"language": "en"}, "ml_signal": ml, "smc": smc or {}}


def test_trader_prompt_follows_the_desk_mode() -> None:
    assert _trader_version(_state(ML_LONG)) is None  # library v7 (ML-led)
    assert _trader_version(_state({})) == TRADER_LLM_ONLY_VERSION
    assert _trader_version(_state({"available": False, "error": "x"})) == TRADER_LLM_ONLY_VERSION == "v6"
    v6 = _system("trade_trader", _state({}), version=_trader_version(_state({})))
    assert "Smart Money" not in v6 and "ML MODEL block" not in v6
    assert _trader_version(_state({}, SMC)) == TRADER_SMC_VERSION == "v8"
    v8 = _system("trade_trader", _state({}, SMC), version=_trader_version(_state({}, SMC)))
    assert "Smart Money Concepts block" in v8 and "ML MODEL block" not in v8
    v7 = _system("trade_trader", _state(ML_LONG), version=_trader_version(_state(ML_LONG)))
    assert "ML MODEL block" in v7


def test_smc_block_text() -> None:
    text = _smc_text(_state({}, SMC))
    assert "SMART MONEY CONCEPTS" in text and "closed 15m bars" in text
    assert "swing structure bullish; breaks (newest first): bullish CHoCH of 2500 7 bars ago" in text
    assert "close in DISCOUNT (30% of the range)" in text
    assert "bullish 2488-2490 (12 bars ago, 1.2 ATR below)" in text
    assert "bullish 2491-2492.5 (3 bars ago, price inside)" in text
    assert "EQH 2519.8 (20 bars ago)" in text
    assert "daily chart: swing structure bullish; internal structure none; swing range not formed yet" in text


def test_smc_block_absent_or_unavailable() -> None:
    assert _smc_text(_state(ML_LONG)) == ""
    assert "unavailable this run (no bars)" in _smc_text(_state({}, {"available": False, "error": "no bars"}))


def test_smc_reading_premium_in_a_rally_is_not_a_short() -> None:
    bull_premium = {
        **SMC,
        "premium_discount": {**SMC["premium_discount"], "position": 0.93, "zone": "premium"},
    }
    text = _smc_text(_state({}, bull_premium))
    assert "the bullish swing structure allows longs only" in text
    assert (
        "wait with a buy limit at the nearest bullish order block / gap below; this is NOT a short signal"
        in text
    )
    assert "daily chart: swing structure bullish; internal structure" in text
    bear_premium = {**bull_premium, "swing": {"trend": "bearish", "breaks": []}}
    assert "price is in premium: a good area for a short" in _smc_text(_state({}, bear_premium))


def test_policy_blocks_a_trade_against_the_smc_swing() -> None:
    rule = SmcStructureAlignment()
    assert not rule.evaluate({"action": "open_short", "smc_swing_trend": "bullish"}, {}).passed
    assert rule.evaluate({"action": "buy", "smc_swing_trend": "bullish"}, {}).passed
    assert not rule.evaluate({"action": "buy", "smc_swing_trend": "bearish"}, {}).passed
    assert rule.evaluate({"action": "open_short", "smc_swing_trend": "bearish"}, {}).passed
    assert rule.evaluate({"action": "open_short", "smc_swing_trend": None}, {}).passed  # no SMC block
    assert rule.evaluate({"action": "hold", "smc_swing_trend": "bullish"}, {}).passed


def test_limits_text_mentions_the_floor_and_the_system_target() -> None:
    from types import SimpleNamespace

    from app.domain.workflows.trading import _limits_text

    text = _limits_text(SimpleNamespace(config={}))  # type: ignore[arg-type]
    assert "Stop floor: a stop closer than 2 x ATR" in text
    assert "FIRST target is set by the system at 1.6R" in text
    off = _limits_text(SimpleNamespace(config={"min_stop_atr": 0, "first_target_r": 0}))  # type: ignore[arg-type]
    assert "Stop floor" not in off and "set by the system" not in off


SETUP_LONG: dict[str, Any] = {
    "active": True,
    "side": "long",
    "level": 2519.8,
    "bars_ago": 2,
    "structure_stop": 2516.0,
    "swing_trend": "bullish",
    "reason": "long setup",
    "candidates": [
        {
            "active": True,
            "type": "EQH",
            "momentum": {"macd_hist_state": "rising_above", "macd_cross": None, "rsi": 61.0, "rsi_ma": 55.0},
        }
    ],
}
SETUP_NONE: dict[str, Any] = {
    "active": False,
    "side": None,
    "reason": "no continuation sweep of an equal high/low",
}


def _gated(setup: dict[str, Any], gate: bool = True) -> dict[str, Any]:
    return _state({}, {**SMC, "setup": setup, "setup_gate": gate})


def test_setup_gate_selects_v9_and_prints_the_setup() -> None:
    from app.domain.workflows.trading import TRADER_SMC_SETUP_VERSION

    assert _trader_version(_gated(SETUP_LONG)) == TRADER_SMC_SETUP_VERSION == "v9"
    assert _trader_version(_gated(SETUP_LONG, gate=False)) == TRADER_SMC_VERSION
    v9 = _system("trade_trader", _gated(SETUP_LONG), version="v9")
    assert "Sweep continuation" in v9 and "No active setup" in v9
    text = _smc_text(_gated(SETUP_LONG))
    assert "SMC SETUP (sweep continuation, computed by code): ACTIVE LONG" in text
    assert "close through the EQH 2519.8 2 bars ago" in text and "structural stop 2516" in text
    assert "MACD hist rising above" in text and "Only a long may open" in text
    assert "SMC SETUP (sweep continuation, computed by code): NONE" in _smc_text(_gated(SETUP_NONE))
    assert "SMC SETUP" not in _smc_text(_gated(SETUP_LONG, gate=False))


def test_setup_policy_and_inputs() -> None:
    from types import SimpleNamespace

    from app.domain.policy import SmcSetupRequired
    from app.domain.workflows.trading import _limits_text, _setup_side

    rule = SmcSetupRequired()
    assert rule.evaluate({"action": "buy", "smc_setup_side": "long"}, {}).passed
    assert not rule.evaluate({"action": "open_short", "smc_setup_side": "long"}, {}).passed
    blocked = rule.evaluate({"action": "buy", "smc_setup_side": "none"}, {})
    assert not blocked.passed and "no active SMC setup" in blocked.message
    assert rule.evaluate({"action": "buy", "smc_setup_side": None}, {}).passed  # no gate
    assert rule.evaluate({"action": "hold", "smc_setup_side": "none"}, {}).passed

    assert _setup_side(_gated(SETUP_LONG)) == "long"
    assert _setup_side(_gated(SETUP_NONE)) == "none"
    assert _setup_side(_gated(SETUP_LONG, gate=False)) is None
    assert _setup_side(_state(ML_LONG)) is None

    ctx = SimpleNamespace(config={})
    gated = _limits_text(ctx, _gated(SETUP_NONE))  # type: ignore[arg-type]
    assert "needs an ACTIVE SMC SETUP" in gated and "against the daily trend" not in gated
    plain = _limits_text(ctx, _gated(SETUP_NONE, gate=False))  # type: ignore[arg-type]
    assert "ACTIVE SMC SETUP" not in plain and "against the daily trend" in plain
