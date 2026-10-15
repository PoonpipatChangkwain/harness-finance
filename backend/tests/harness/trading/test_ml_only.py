"""ml_only: with a long/short ML signal the trade is the model's own label — no analysts, no LLM."""

from __future__ import annotations

from typing import Any

from app.domain.compute.ml.bull_bear import _atr_multiple
from app.domain.workflows.trading import _is_ml_only, _ml_proposal, route_after_context

ML: dict[str, Any] = {
    "available": True,
    "model": "xau_swing_ct25",
    "signal": "long",
    "confidence": 0.5,
    "p_bull": 0.5,
    "p_bear": 0.1,
    "stop": "2.0 x ATR14 (Wilder)",
    "stop_atr_multiple": 2.0,
    "tp_r_long": 2.5,
    "tp_r_short": 1.5,
    "time_stop_bars": 48,
}


def _state(ml: dict[str, Any], *, ml_only: bool) -> dict[str, Any]:
    return {
        "input": {"ml_only": ml_only, "horizon": "swing"},
        "ml_signal": ml,
        "market_snapshot": {"available": True},
    }


def test_atr_multiple_parses_the_label_risk() -> None:
    assert _atr_multiple("2.0 x ATR14 (Wilder)") == 2.0
    assert _atr_multiple("1.5 x ATR14 (Wilder)") == 1.5
    assert _atr_multiple("fixed 10 pips") is None


def test_ml_only_skips_analysts_only_for_a_directional_signal() -> None:
    assert route_after_context(_state(ML, ml_only=True)) == "ml_direct"
    assert route_after_context(_state(ML, ml_only=False)) == "ok"
    assert route_after_context(_state({**ML, "signal": "flat"}, ml_only=True)) == "ml_flat"
    # model unavailable → never silently skip the desk
    assert route_after_context(_state({"available": False, "error": "x"}, ml_only=True)) == "ok"
    assert not _is_ml_only(_state({}, ml_only=True))


def test_proposal_is_the_models_label_per_side() -> None:
    long_ = _ml_proposal(_state(ML, ml_only=True))
    assert (long_["side"], long_["atr_multiple"], long_["target_r"], long_["time_stop_bars"]) == (
        "long",
        2.0,
        [2.5],
        48,
    )
    assert long_["order_type"] == "market" and long_["target_levels"] == []
    short = _ml_proposal(_state({**ML, "signal": "short"}, ml_only=True))
    assert (short["side"], short["target_r"]) == ("short", [1.5])
