"""Structured trading outputs coerce LLM-written numbers instead of failing the whole call (T5)."""

from __future__ import annotations

from app.domain.agents.schemas import PortfolioDecision, TraderProposal

DECISION = {"verdict": "APPROVE", "confidence": 0.7, "reason": "ok"}


def test_trader_prices_drop_percentages_and_ranges_but_keep_the_rest() -> None:
    p = TraderProposal.model_validate(
        {
            "side": "long",
            "entry_zone": ["$34.20", "34.8"],
            "stop_basis": "level",
            "stop_level": "5%",  # a distance, not a price: dropped, not read as 5.0
            "atr_multiple": "N/A",
            "target_levels": ["37", "38-39", "around 40", 41.5],
            "decision": DECISION,
        }
    )
    assert p.entry_zone == [34.2, 34.8]
    assert p.stop_level is None and p.atr_multiple is None
    assert p.target_levels == [37.0, 41.5]


def test_pm_size_fraction_accepts_a_percentage() -> None:
    pm = PortfolioDecision.model_validate({"rating": "Buy", "decision": DECISION, "final_size_pct": "50%"})
    assert pm.final_size_pct == 0.5
