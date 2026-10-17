"""(P17) Memory / ReasoningBank / discovery schemas (contracts.md §13.2).

LLM-facing models (``ExtractedFacts``, ``ReconcileOps``, ``DiscoveryAssessment``,
``DistilledLessons``, ``PrivacyVerdict``, ``OutcomeJudgement``) are lenient on input: numbers
are clamped and over-long lists are cut instead of failing validation, so a slightly sloppy
model still yields a usable value. Anything that cannot be coerced fails the structured call,
and the engine then writes nothing (fail-safe).

Read models (``MemoryItemModel``, ``ReasoningItem``) mirror the storage rows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MemoryCategory = Literal[
    "investment_style",
    "risk_tolerance",
    "risk_capacity",
    "goal",
    "constraint",
    "preference",
    "life_fact",
    "behavioral_pattern",
    "knowledge_level",
]
CATEGORIES: tuple[MemoryCategory, ...] = (
    "investment_style",
    "risk_tolerance",
    "risk_capacity",
    "goal",
    "constraint",
    "preference",
    "life_fact",
    "behavioral_pattern",
    "knowledge_level",
)
MemorySource = Literal[
    "user_stated", "user_confirmed", "inferred_behavior", "questionnaire", "portfolio_data", "manual"
]
MemoryStatus = Literal["candidate", "active", "superseded", "retired", "rejected"]
MEMORY_STATUSES: tuple[MemoryStatus, ...] = ("candidate", "active", "superseded", "retired", "rejected")
Sensitivity = Literal["normal", "financial", "pii"]
ReconcileOpKind = Literal["NOOP", "ADD", "UPDATE", "SUPERSEDE", "RETIRE"]

# Sources that count as the user's own word: an inference may never override them.
STATED_SOURCES: frozenset[str] = frozenset({"user_stated", "user_confirmed", "manual", "questionnaire"})
# Categories that need the user's own word before they become active.
CONFIRM_CATEGORIES: frozenset[str] = frozenset({"risk_tolerance", "goal", "constraint"})
ACTIVE_WITHOUT_CONFIRM_SOURCES: frozenset[str] = frozenset({"user_stated", "user_confirmed", "manual"})

RBKind = Literal["strategy", "pitfall", "coaching"]
RBStatus = Literal["candidate", "active", "retired"]
OutcomeSignal = Literal["success", "failure", "mixed"]
RBWorkflow = Literal["research", "portfolio", "trade"]


def _clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(lo, min(hi, number))


def _clamp01(value: Any) -> float:
    return _clamp(value, 0.0, 1.0, 0.0)


def _importance(value: Any) -> int:
    return round(_clamp(value, 1, 10, 5))


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


# -- user memory: read model --------------------------------------------------------------------


class MemoryItemModel(BaseModel):
    """One ``user_memory`` row."""

    id: str
    user_id: str
    category: MemoryCategory
    key: str | None = None
    text: str
    value_json: dict[str, Any] | None = None
    confidence: float
    importance: int
    source: MemorySource
    source_ref: dict[str, Any] = Field(default_factory=dict)
    status: MemoryStatus
    supersedes: str | None = None
    superseded_by: str | None = None
    valid_from: datetime
    valid_to: datetime | None = None
    sensitivity: Sensitivity = "normal"
    created_at: datetime
    updated_at: datetime
    last_accessed_at: datetime | None = None
    access_count: int = 0

    @property
    def flags(self) -> list[str]:
        flags = self.source_ref.get("flags") or []
        return [str(f) for f in flags] if isinstance(flags, list) else []


# -- user memory: LLM schemas -------------------------------------------------------------------


class ExtractedFact(BaseModel):
    """One durable investor fact pulled from the user's own messages."""

    category: MemoryCategory
    key: str | None = Field(default=None, max_length=120, description="Slot of a single-valued fact")
    text: str = Field(min_length=3, max_length=800, description='One self-contained sentence: "User …"')
    value_json: dict[str, Any] | None = None
    confidence: float = Field(default=0.7, description="0..1")
    importance: int = Field(default=5, description="1..10")
    source: Literal["user_stated", "inferred_behavior"] = "user_stated"
    sensitivity: Sensitivity = "normal"

    _conf = field_validator("confidence", mode="before")(_clamp01)
    _imp = field_validator("importance", mode="before")(_importance)
    _key = field_validator("key", mode="before")(_blank_to_none)


MAX_FACTS_PER_TURN = 5


class ExtractedFacts(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list, description=f"At most {MAX_FACTS_PER_TURN}")

    @field_validator("facts", mode="after")
    @classmethod
    def _cap(cls, facts: list[ExtractedFact]) -> list[ExtractedFact]:
        return facts[:MAX_FACTS_PER_TURN]


class ReconcileOp(BaseModel):
    op: ReconcileOpKind
    target_id: str | None = Field(default=None, description="Existing memory id (never invented)")
    text: str = Field(default="", max_length=800)
    value_json: dict[str, Any] | None = None
    confidence: float = 0.7
    importance: int = 5
    reason: str = Field(default="", max_length=500)

    _conf = field_validator("confidence", mode="before")(_clamp01)
    _imp = field_validator("importance", mode="before")(_importance)
    _target = field_validator("target_id", mode="before")(_blank_to_none)

    @field_validator("op", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip().upper()
            return {"NONE": "NOOP", "DELETE": "RETIRE"}.get(text, text)
        return value


class ReconcileOps(BaseModel):
    ops: list[ReconcileOp] = Field(default_factory=list)


class MemoryChange(BaseModel):
    """What one ``learn`` / manual write did to a memory item."""

    op: ReconcileOpKind
    item: MemoryItemModel
    previous: MemoryItemModel | None = None
    reason: str | None = None


# -- discovery (5 Whys, research §B.4) ----------------------------------------------------------

RequestType = Literal["buy_single", "sell_all", "concentrate", "return_target", "income", "info", "other"]
ALLOCATION_REQUESTS: frozenset[str] = frozenset(
    {"buy_single", "sell_all", "concentrate", "return_target", "income"}
)
GoalCategory = Literal[
    "retirement",
    "home",
    "education",
    "income",
    "wealth_growth",
    "safety",
    "speculation_fun",
    "legacy",
    "other",
]
EmotionalDriver = Literal["fomo", "fear", "greed", "boredom", "none"]
StopReason = Literal[
    "root_goal", "max_whys", "resistance", "urgency", "informational", "memory_already_known"
]
AdviceMode = Literal["normal", "stabilise", "educate"]


class WhyStep(BaseModel):
    q: str = ""
    a: str = ""


class RootGoal(BaseModel):
    category: GoalCategory = "other"
    amount: float | None = None
    currency: str | None = Field(default=None, max_length=8)
    horizon_years: float | None = None
    text: str = ""


class MemoryCandidate(BaseModel):
    category: MemoryCategory = "goal"
    text: str = Field(min_length=3, max_length=800)
    confidence: float = 0.7
    key: str | None = Field(default=None, max_length=120)

    _conf = field_validator("confidence", mode="before")(_clamp01)
    _key = field_validator("key", mode="before")(_blank_to_none)


class DiscoveryAssessment(BaseModel):
    request_type: RequestType = "other"
    whys_asked: int = Field(default=0, ge=0)
    chain: list[WhyStep] = Field(default_factory=list)
    root_goal_identified: bool = False
    root_goal: RootGoal | None = None
    emotional_driver: EmotionalDriver = "none"
    confidence: float = 0.0
    next_question: str | None = Field(default=None, max_length=600)
    stop_reason: StopReason | None = None
    advice_mode: AdviceMode = "normal"
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    answers_pending_question: bool = False

    _conf = field_validator("confidence", mode="before")(_clamp01)
    _next = field_validator("next_question", "stop_reason", mode="before")(_blank_to_none)

    @field_validator("whys_asked", mode="before")
    @classmethod
    def _non_negative(cls, value: Any) -> int:
        return round(_clamp(value, 0, 100, 0))


# -- ReasoningBank -----------------------------------------------------------------------------


class ReasoningItem(BaseModel):
    """One ``reasoning_bank`` row as callers see it (the producing user is never exposed)."""

    id: str
    title: str
    description: str
    content: str
    kind: RBKind
    outcome_signal: OutcomeSignal
    judge_score: float | None = None
    tags: dict[str, Any] = Field(default_factory=dict)
    use_count: int = 0
    success_count: int = 0
    success_rate: float = 0.5
    source_count: int = 1
    status: RBStatus
    prompt_version: str
    model: str | None = None
    source_run_id: str | None = None
    created_at: datetime
    updated_at: datetime


def success_rate(success_count: int, use_count: int) -> float:
    """Bayesian estimate (Laplace): an unused item starts at 0.5."""
    return (success_count + 1) / (use_count + 2)


MAX_LESSONS = 3


class DistilledLesson(BaseModel):
    title: str = Field(min_length=3, max_length=400)
    description: str = Field(min_length=3, max_length=600)
    content: str = Field(min_length=3, max_length=2000)
    kind: RBKind = "strategy"
    tags: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", mode="after")
    @classmethod
    def _short_title(cls, value: str) -> str:
        return value.strip()[:120]

    @field_validator("content", mode="after")
    @classmethod
    def _max_words(cls, value: str) -> str:
        words = value.split()
        return " ".join(words[:150]) if len(words) > 150 else value.strip()


class DistilledLessons(BaseModel):
    items: list[DistilledLesson] = Field(default_factory=list, description=f"At most {MAX_LESSONS}")

    @field_validator("items", mode="after")
    @classmethod
    def _cap(cls, items: list[DistilledLesson]) -> list[DistilledLesson]:
        return items[:MAX_LESSONS]


class PrivacyVerdict(BaseModel):
    identifies_person_or_holdings: bool
    reason: str = ""


class OutcomeJudgement(BaseModel):
    success: bool
    score: float = 0.5
    reason: str = ""

    _score = field_validator("score", mode="before")(_clamp01)


# -- recall ---------------------------------------------------------------------------------------


class Recall(BaseModel):
    """What a workflow gets back from ``MemoryPort.recall``."""

    profile_text: str = ""
    token_estimate: int = 0
    # the active items rendered into ``profile_text`` (e.g. the advisor's goal fallback reads them)
    profile_items: list[MemoryItemModel] = Field(default_factory=list)
    items: list[MemoryItemModel] = Field(default_factory=list)
    reasoning: list[ReasoningItem] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
