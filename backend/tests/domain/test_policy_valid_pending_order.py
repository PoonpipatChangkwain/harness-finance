"""``ValidPendingOrder``: a market order passes; a limit/stop passes only without a computed error."""

from __future__ import annotations

from app.domain.agents.schemas import TraderProposal
from app.domain.policy import ValidPendingOrder


def test_valid_pending_order() -> None:
    rule = ValidPendingOrder()
    assert rule.evaluate({"order": "market"}, {}).passed is True
    assert rule.evaluate({}, {}).passed is True  # no order field = market
    ok = rule.evaluate({"order": "sell_limit", "order_error": None, "order_distance_atr": 1.2}, {})
    assert ok.passed is True and ok.message == "sell limit is valid"
    bad = rule.evaluate({"order": "buy_limit", "order_error": "buy limit 35 must be below 34.5"}, {})
    assert bad.passed is False and bad.severity == "block" and "must be below" in bad.message


def test_trader_proposal_coerces_order_fields() -> None:
    base = {"side": "short", "decision": {"verdict": "APPROVE", "confidence": 0.8, "reason": "x"}}
    p = TraderProposal.model_validate({**base, "order_type": "Sell Limit", "order_price": "4,188",
                                       "order_valid_bars": "12 bars"})  # fmt: skip
    assert (p.order_type, p.order_price, p.order_valid_bars) == ("limit", 4188.0, 12)
    assert TraderProposal.model_validate({**base, "order_type": "buy_stop"}).order_type == "stop"
    assert TraderProposal.model_validate({**base, "order_type": "now"}).order_type == "market"
    assert TraderProposal.model_validate(base).order_type == "market"
