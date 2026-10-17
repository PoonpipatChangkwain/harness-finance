"""(P17) 5-Whys routing rule (research §B.3; phase-17 §17.2 ``route_after_assess``).

Pure. Never blocks the user: a missing/unparsed assessment, a skip click, the why budget, a
resisting or panicking user, or an already-understood goal all route to plain ``research``.

- ``ask_only``: the user is answering our previous why, the root goal is still unknown, no new
  information request in this message (``request_type == "other"``) → only the next question
  (no research cost).
- ``research_then_ask``: an allocation-changing request (buy/sell/concentrate/return/income)
  whose goal is not understood yet (``confidence < threshold``) → answer first, then one why.
  When memory already holds the goal (``stop_reason == "memory_already_known"``) the question is
  a confirmation instead, asked once the same way.
- ``research``: everything else.
"""

from __future__ import annotations

from typing import Literal

from app.domain.memory.schemas import ALLOCATION_REQUESTS, AdviceMode, DiscoveryAssessment

DiscoveryRoute = Literal["ask_only", "research", "research_then_ask"]
_HARD_STOPS = frozenset({"resistance", "urgency", "max_whys"})


def advice_mode_of(assessment: DiscoveryAssessment | None) -> AdviceMode:
    """``stabilise`` on urgency (panic, margin call: facts and options, no more whys).

    Fail-safe ``normal`` when there is no assessment.
    """
    if assessment is None:
        return "normal"
    if assessment.stop_reason == "urgency":
        return "stabilise"
    return assessment.advice_mode


def should_ask(
    assessment: DiscoveryAssessment | None,
    whys_asked: int,
    skip: bool,
    *,
    max_whys: int = 5,
    confidence_threshold: float = 0.7,
) -> DiscoveryRoute:
    if assessment is None or skip or whys_asked >= max_whys:
        return "research"
    if assessment.stop_reason in _HARD_STOPS or advice_mode_of(assessment) == "stabilise":
        return "research"
    if assessment.stop_reason == "memory_already_known":
        if assessment.request_type in ALLOCATION_REQUESTS and assessment.next_question:
            return "research_then_ask"
        return "research"
    if assessment.root_goal_identified and assessment.confidence >= confidence_threshold:
        return "research"
    if assessment.stop_reason is not None:  # root_goal / informational
        return "research"
    if (
        assessment.answers_pending_question
        and not assessment.root_goal_identified
        and assessment.request_type == "other"
    ):
        return "ask_only"
    if (
        assessment.request_type in ALLOCATION_REQUESTS
        and assessment.confidence < confidence_threshold
        and assessment.next_question
    ):
        return "research_then_ask"
    return "research"
