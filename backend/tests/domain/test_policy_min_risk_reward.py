"""``MinRiskReward``: strictly greater than the limit, fail closed without a target, flat passes."""

from __future__ import annotations

from app.domain.policy import MinRiskReward


def test_min_risk_reward() -> None:
    rule = MinRiskReward(1.0)
    assert rule.evaluate({"action": "buy", "risk_reward": 1.01}, {}).passed is True
    assert rule.evaluate({"action": "buy", "risk_reward": 1.0}, {}).passed is False  # must exceed
    assert rule.evaluate({"action": "open_short", "risk_reward": 0.5}, {}).passed is False
    missing = rule.evaluate({"action": "buy", "risk_reward": None}, {})
    assert missing.passed is False and "unknown" in missing.message  # no target fails closed
    assert rule.evaluate({"action": "hold"}, {}).passed is True  # opens no risk
