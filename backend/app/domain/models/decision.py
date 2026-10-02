"""Decision schema shared by every routing point in the graph (T5).

Ported from ``../TradingAgents/tradingagents/agents/rating.py``
(5-tier rating scale + heuristic parser) and the structured-decision
pattern in ``../TradingAgents/tradingagents/agents/structured.py``.
See docs/contracts.md §8.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

from pydantic import BaseModel, Field


class Verdict(StrEnum):
    APPROVE = "APPROVE"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


class Rating(StrEnum):
    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class Decision(BaseModel):
    verdict: Verdict
    confidence: float = Field(ge=0, le=1)
    reason: str
    next_action: str | None = None


def route_decision(decision: Decision | None, *, min_confidence: float = 0.6) -> Verdict:
    """Fail-safe routing: None, low confidence, or REJECT all resolve safely.

    Never defaults to APPROVE. A decision that could not be parsed or that
    falls below the confidence threshold is treated as REVIEW so a human (or
    a stricter re-check) sees it, rather than silently letting a bad
    proposal through.
    """
    if decision is None:
        return Verdict.REVIEW
    if decision.verdict == Verdict.REJECT:
        return Verdict.REJECT
    if decision.confidence < min_confidence:
        return Verdict.REVIEW
    return decision.verdict


_RATING_SET = {r.value.lower() for r in Rating}
_RATING_WORDS = "|".join(r.value for r in Rating)
_DASH_CLASS = "‐-―"  # en/em dash variants a model may write instead of "-"  # noqa: RUF001
_RATING_LABEL_RE = re.compile(rf"rating\b[^:\-{_DASH_CLASS}]*[:\-{_DASH_CLASS}][\s*]*(\w+)", re.IGNORECASE)
_RATING_SCALE_RE = re.compile(r"rating\s*(scale|options|legend)", re.IGNORECASE)
_RATING_WORD_RE = re.compile(rf"\b({_RATING_WORDS})\b", re.IGNORECASE)


def extract_rating(text: str) -> Rating | None:
    """Extract a 5-tier rating from prose, or None if none is present.

    Ported from ``rating.py::extract_rating``: prefers an explicit
    "Rating: X" label (last one written), else a single unambiguous rating
    word; several distinct words with no label is treated as ambiguous.
    """
    if not text:
        return None
    norm = unicodedata.normalize("NFKC", text)

    labelled: str | None = None
    for line in norm.splitlines():
        if _RATING_SCALE_RE.search(line):
            continue
        m = _RATING_LABEL_RE.search(line)
        if m and m.group(1).lower() in _RATING_SET:
            labelled = m.group(1).capitalize()
    if labelled:
        return Rating(labelled)

    named = {m.group(1).capitalize() for m in _RATING_WORD_RE.finditer(norm)}
    if len(named) == 1:
        return Rating(named.pop())
    return None


def parse_rating(text: str) -> Rating | None:
    return extract_rating(text)
