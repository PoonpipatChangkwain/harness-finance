"""Event payload models + envelope (contracts.md §10).

Every event type has a Pydantic payload model; ``EventBus.emit`` validates
against it so a typo in a payload fails in tests instead of silently
reaching the UI. ``python -m app.harness.events.export`` writes the JSON
schema of the discriminated envelope union to
``backend/schemas/events.schema.json`` for the frontend to test against.
Ported event vocabulary from ``../dexter/src/agent/types.ts``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunStarted(_Payload):
    workflow: str
    input: dict[str, Any]
    graph: dict[str, Any] | None = None


class RunFinished(_Payload):
    status: Literal["completed", "failed", "cancelled", "interrupted", "limited"]
    error: str | None = None
    reason: str | None = None


class NodeStarted(_Payload):
    node: str
    label: str
    phase: str


class NodeFinished(_Payload):
    node: str
    status: Literal["done", "error", "skipped"]
    duration_ms: int
    summary: str | None = None


class LLMStarted(_Payload):
    provider: str
    model: str
    tier: Literal["deep", "quick"]
    purpose: str


class LLMDeltaPayload(_Payload):
    channel: Literal["reasoning", "answer"]
    text: str


class LLMFinished(_Payload):
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int
    cache_read_tokens: int = 0
    structured_mode: Literal["native", "fallback_parsed", "failed"] | None = None


class ToolStarted(_Payload):
    tool: str
    category: str
    args: dict[str, Any]
    read_only: bool
    batch_id: str


class ToolProgress(_Payload):
    message: str


class ToolErrorPayload(_Payload):
    type: str
    message: str
    retryable: bool


class ToolFinished(_Payload):
    call_id: str
    tool: str
    ok: bool
    status: Literal["ok", "partial", "unavailable", "error", "denied", "limited", "timeout", "cancelled"]
    summary: str
    preview: str
    truncated: bool
    error: ToolErrorPayload | None = None
    provenance_ids: list[str]
    duration_ms: int


class ContextUsage(_Payload):
    used_tokens: int
    context_window: int
    percent: float


class ContextMicrocompact(_Payload):
    cleared: int
    tokens_saved: int


class ContextCompaction(_Payload):
    phase: Literal["start", "end", "failed", "fallback_clear"]
    pre_tokens: int
    post_tokens: int | None = None
    summary: str | None = None
    cleared: int | None = None  # fallback_clear: messages removed
    kept: int | None = None  # fallback_clear: tool results kept


class GuardLimit(_Payload):
    kind: Literal["iterations", "tool_calls", "similar_query", "timeout", "budget"]
    detail: str


class Warning(_Payload):
    code: str
    message: str


class ApprovalRequired(_Payload):
    approval_id: str
    kind: str
    title: str
    proposal: dict[str, Any]
    policy_results: list[dict[str, Any]]
    provenance_ids: list[str]


class ApprovalResolved(_Payload):
    approval_id: str
    outcome: Literal["approved", "rejected", "edited"]
    edits: dict[str, Any] | None = None


class Citation(_Payload):
    marker: int
    provenance_id: str


class AnswerDiscovery(_Payload):
    """(P17) The answer ends with a 5-Whys question: progress ``whys_asked``/``max`` + the question."""

    whys_asked: int
    max: int
    question: str


class AnswerFinal(_Payload):
    markdown: str
    citations: list[Citation]
    unverified_numbers: list[str]
    discovery: AnswerDiscovery | None = None  # (P17)


class CheckpointSaved(_Payload):
    node: str
    thread_id: str


class BacktestProgress(_Payload):
    """(P15) Where a backtest stands: phase and cell counts, cost of the LLM cells so far."""

    phase: Literal["preload", "decisions", "simulate", "done"]
    total: int = 0
    done: int = 0
    cached: int = 0
    failed: int = 0
    running: int = 0
    cost_usd: float = 0.0
    message: str | None = None


class BacktestCell(_Payload):
    """(P15) One (symbol, date) cell changed state; ``run_id`` is its trading run."""

    cell_id: str
    symbol: str
    day: str
    status: Literal["pending", "running", "done", "failed"]
    run_id: str | None = None
    rating: str | None = None
    side: str | None = None
    outcome: str | None = None
    cached: bool = False
    error: str | None = None


class BacktestSim(_Payload):
    """(P15) A portfolio simulation finished."""

    sim_id: str
    final_equity: str
    total_return: float
    trades: int


class MemoryRecalledItem(_Payload):
    id: str
    category: str
    text: str
    status: str = "active"


class MemoryRecalledReasoning(_Payload):
    id: str
    title: str


class MemoryRecalled(_Payload):
    """(P17) What the run got from long-term memory (before any ``llm.*`` of the run)."""

    items: list[MemoryRecalledItem]
    reasoning: list[MemoryRecalledReasoning]


class DiscoveryRootGoal(_Payload):
    text: str
    category: str | None = None


class DiscoveryAssessed(_Payload):
    """(P17) The 5-Whys assessment of this turn."""

    whys_asked: int
    root_goal_identified: bool
    confidence: float
    stop_reason: str | None = None
    advice_mode: Literal["normal", "stabilise", "educate"] = "normal"
    next_question: str | None = None
    root_goal: DiscoveryRootGoal | None = None  # the "เข้าใจเป้าหมายแล้ว: …" chip


class MemoryChangePayload(_Payload):
    op: Literal["ADD", "UPDATE", "SUPERSEDE", "RETIRE", "NOOP"]
    id: str
    category: str
    text: str
    previous_text: str | None = None
    status: str


class MemoryUpdated(_Payload):
    """(P17) What ``learn`` wrote (NOOPs are not sent)."""

    changes: list[MemoryChangePayload]


class ReasoningDistilledItem(_Payload):
    id: str
    title: str
    kind: Literal["strategy", "pitfall", "coaching"]


class ReasoningDistilled(_Payload):
    """(P17) Lessons added to (or merged into) the ReasoningBank."""

    items: list[ReasoningDistilledItem]


PAYLOADS: dict[str, type[_Payload]] = {
    "backtest.progress": BacktestProgress,
    "backtest.cell": BacktestCell,
    "backtest.sim": BacktestSim,
    "run.started": RunStarted,
    "run.finished": RunFinished,
    "node.started": NodeStarted,
    "node.finished": NodeFinished,
    "llm.started": LLMStarted,
    "llm.delta": LLMDeltaPayload,
    "llm.finished": LLMFinished,
    "tool.started": ToolStarted,
    "tool.progress": ToolProgress,
    "tool.finished": ToolFinished,
    "context.usage": ContextUsage,
    "context.microcompact": ContextMicrocompact,
    "context.compaction": ContextCompaction,
    "guard.limit": GuardLimit,
    "warning": Warning,
    "approval.required": ApprovalRequired,
    "approval.resolved": ApprovalResolved,
    "answer.final": AnswerFinal,
    "checkpoint.saved": CheckpointSaved,
    "memory.recalled": MemoryRecalled,
    "discovery.assessed": DiscoveryAssessed,
    "memory.updated": MemoryUpdated,
    "reasoning.distilled": ReasoningDistilled,
}

EventType = str


class EventEnvelope(BaseModel):
    """What the SSE stream carries. ``ephemeral`` live-only deltas have no stored seq of their own."""

    seq: int
    run_id: str
    session_id: str | None = None
    ts: datetime
    type: EventType
    node: str | None = None
    call_id: str | None = None
    parent_call_id: str | None = None
    payload: dict[str, Any]
    ephemeral: bool = False


def validate_payload(type_: str, payload: dict[str, Any]) -> dict[str, Any]:
    model = PAYLOADS.get(type_)
    if model is None:
        raise ValueError(f"unknown event type {type_!r}")
    return model.model_validate(payload).model_dump(mode="json")


def _typed_envelopes() -> list[type[BaseModel]]:
    out: list[type[BaseModel]] = []
    for type_, payload_model in PAYLOADS.items():
        name = "Event_" + type_.replace(".", "_")
        out.append(
            create_model(
                name,
                __base__=EventEnvelope,
                type=(Literal[type_], ...),
                payload=(payload_model, ...),
            )
        )
    return out


def envelope_json_schema() -> dict[str, Any]:
    envelopes = _typed_envelopes()
    union = Annotated[Union[tuple(envelopes)], Field(discriminator="type")]  # type: ignore[valid-type]  # noqa: UP007
    schema: dict[str, Any] = TypeAdapter(union).json_schema()
    schema["title"] = "EventEnvelope"
    return schema
