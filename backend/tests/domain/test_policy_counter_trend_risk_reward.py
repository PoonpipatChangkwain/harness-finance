"""``CounterTrendRiskReward``: only fires on a counter-trend trade, stricter limit, fail closed."""

from __future__ import annotations

from app.domain.policy import CounterTrendRiskReward


def test_counter_trend_risk_reward() -> None:
    rule = CounterTrendRiskReward(2.5)
    assert rule.evaluate({"counter_trend": False, "action": "buy", "risk_reward": 1.0}, {}).passed is True
    with_trend = rule.evaluate({"counter_trend": True, "action": "buy", "risk_reward": 2.6}, {})
    assert with_trend.passed is True
    assert rule.evaluate({"counter_trend": True, "action": "buy", "risk_reward": 2.5}, {}).passed is False
    assert rule.evaluate({"counter_trend": True, "action": "open_short", "risk_reward": 1.0}, {}).passed is False
    missing = rule.evaluate({"counter_trend": True, "action": "buy", "risk_reward": None}, {})
    assert missing.passed is False and "unknown" in missing.message
    assert rule.evaluate({"counter_trend": True, "action": "hold"}, {}).passed is True  # opens no risk
    assert rule.evaluate({"counter_trend": False}, {}).passed is True  # not counter-trend: passes outright
