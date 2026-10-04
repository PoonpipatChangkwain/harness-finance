"""The ML block the desk reads: shown when a model ran, says 'unavailable' on failure, absent otherwise."""

from __future__ import annotations

from typing import Any

from app.domain.workflows.trading import _ml_text

ML: dict[str, Any] = {
    "available": True,
    "model": "xau_intraday_ct25",
    "version": "xau-bullbear-0.2.0",
    "timeframe": "15m",
    "bar_ts": "2026-09-09T23:45:00+00:00",
    "p_bull": 0.41,
    "p_bear": 0.18,
    "signal": "long",
    "tau": 0.375,
    "stop": "1.5 x ATR14 (Wilder)",
    "tp_r_long": 2.5,
    "tp_r_short": 1.5,
    "time_stop_bars": 16,
    "daily_trend": "down",
    "track_record": {
        "period": "walk-forward 2025-06..2026-01",
        "model": {"trades": 79, "win_rate": 0.48, "profit_factor": 1.08, "long_share": 0.85},
        "always_long": {"win_rate": 0.43, "profit_factor": 0.98},
    },
}


def test_ml_block_carries_probabilities_signal_and_track_record() -> None:
    text = _ml_text({"ml_signal": ML})
    assert "P(long wins) = 0.41" in text and "+2.5R" in text
    assert "P(short wins) = 0.18" in text
    assert "model signal: LONG" in text
    assert "79 trades, win 48%" in text and "always-long: win 43%" in text


def test_failed_model_is_reported_not_hidden() -> None:
    text = _ml_text({"ml_signal": {"available": False, "error": "15m bars unavailable"}})
    assert "unavailable" in text and "15m bars unavailable" in text


def test_no_model_for_the_symbol_adds_nothing() -> None:
    assert _ml_text({"ml_signal": {}}) == ""
    assert _ml_text({}) == ""
