"""``_counter_trend``: side vs. daily SMA50/SMA200; unknown data never counts as counter-trend."""

from __future__ import annotations

from app.domain.workflows.trading import _counter_trend


def _state(**htf: object) -> dict[str, object]:
    return {"market_snapshot": {"higher_tf": htf} if htf else {}}


def test_short_above_both_daily_smas_is_counter_trend() -> None:
    state = _state(close=110.0, indicators={"sma50": 100.0, "sma200": 95.0})
    assert _counter_trend(state, "short") is True
    assert _counter_trend(state, "long") is False


def test_long_below_both_daily_smas_is_counter_trend() -> None:
    state = _state(close=90.0, indicators={"sma50": 100.0, "sma200": 105.0})
    assert _counter_trend(state, "long") is True
    assert _counter_trend(state, "short") is False


def test_between_the_two_smas_is_not_counter_trend_either_way() -> None:
    state = _state(close=98.0, indicators={"sma50": 100.0, "sma200": 95.0})
    assert _counter_trend(state, "short") is False
    assert _counter_trend(state, "long") is False


def test_missing_higher_timeframe_or_smas_fails_open() -> None:
    assert _counter_trend(_state(), "short") is False
    assert _counter_trend(_state(close=110.0, indicators={}), "short") is False
    assert _counter_trend({"market_snapshot": {}}, "short") is False


def test_flat_side_is_never_counter_trend() -> None:
    state = _state(close=110.0, indicators={"sma50": 100.0, "sma200": 95.0})
    assert _counter_trend(state, "flat") is False
    assert _counter_trend(state, None) is False
