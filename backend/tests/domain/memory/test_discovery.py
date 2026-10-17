"""(P17) ``should_ask``: every branch of the 5-Whys router (research §B.3); never blocks the user."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.memory.discovery import advice_mode_of, should_ask
from app.domain.memory.schemas import DiscoveryAssessment


def a(**kw: Any) -> DiscoveryAssessment:
    base: dict[str, Any] = {
        "request_type": "buy_single",
        "confidence": 0.3,
        "next_question": "ถ้าได้ถือ NVDA แล้วจะช่วยอะไรคุณ?",
    }
    return DiscoveryAssessment.model_validate(base | kw)


def test_parse_failure_skip_and_budget_go_to_research() -> None:
    assert should_ask(None, 0, False) == "research"
    assert should_ask(a(), 0, True) == "research"
    assert should_ask(a(), 5, False) == "research"
    assert should_ask(a(), 3, False, max_whys=3) == "research"


def test_allocation_request_with_unknown_goal_asks_after_research() -> None:
    assert should_ask(a(), 0, False) == "research_then_ask"
    assert should_ask(a(request_type="concentrate"), 4, False) == "research_then_ask"
    assert should_ask(a(next_question=None), 0, False) == "research"  # nothing to ask
    assert should_ask(a(confidence=0.7), 0, False) == "research"
    assert should_ask(a(confidence=0.6), 0, False, confidence_threshold=0.5) == "research"


def test_answer_to_pending_question_asks_only() -> None:
    answer = a(request_type="other", answers_pending_question=True, whys_asked=1)
    assert should_ask(answer, 1, False) == "ask_only"
    assert should_ask(answer.model_copy(update={"request_type": "info"}), 1, False) == "research"
    assert should_ask(answer, 1, True) == "research"


def test_identified_goal_and_stop_reasons_go_to_research() -> None:
    assert should_ask(a(root_goal_identified=True, confidence=0.8), 2, False) == "research"
    for reason in ("resistance", "urgency", "max_whys", "root_goal", "informational"):
        assert should_ask(a(stop_reason=reason), 1, False) == "research", reason
    assert should_ask(a(advice_mode="stabilise"), 0, False) == "research"


def test_memory_already_known_confirms_once() -> None:
    known = a(
        stop_reason="memory_already_known", confidence=0.9, next_question="ยังเก็บเงินดาวน์คอนโดปี 2028 อยู่ไหม"
    )
    assert should_ask(known, 0, False) == "research_then_ask"
    assert should_ask(known.model_copy(update={"request_type": "info"}), 0, False) == "research"
    assert should_ask(known, 5, False) == "research"


@pytest.mark.parametrize(
    ("assessment", "mode"),
    [
        (None, "normal"),
        (DiscoveryAssessment(stop_reason="urgency"), "stabilise"),
        (DiscoveryAssessment(advice_mode="educate"), "educate"),
        (DiscoveryAssessment(), "normal"),
    ],
)
def test_advice_mode(assessment: DiscoveryAssessment | None, mode: str) -> None:
    assert advice_mode_of(assessment) == mode


def test_assessment_is_lenient() -> None:
    parsed = DiscoveryAssessment.model_validate(
        {"confidence": 7, "whys_asked": -2, "next_question": "  ", "stop_reason": ""}
    )
    assert parsed.confidence == 1.0
    assert parsed.whys_asked == 0
    assert parsed.next_question is None and parsed.stop_reason is None
