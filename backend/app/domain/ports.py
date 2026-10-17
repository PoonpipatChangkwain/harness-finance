"""Hexagonal ports: Protocols that `app.domain` depends on but does not implement.

`app.harness` implements these (LLMPort in harness/llm, ToolRunnerPort in
harness/executor, EventPort in harness/events, NodeContext in
harness/graph) so that domain code can call "the LLM" or "emit an event"
without importing any framework. LLMPort lands in Phase 04, ToolRunnerPort
and EventPort in Phase 05; Phase 09 adds NodeContext.
See docs/contracts.md §§6-10.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.domain.tools.base import ToolSpec

Tier = Literal["deep", "quick"]
Role = Literal["system", "user", "assistant", "tool"]
StructuredMode = Literal["native", "fallback_parsed", "failed"]


class ToolCallReq(BaseModel):
    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class LLMMessage(BaseModel):
    role: Role
    content: str
    tool_calls: list[ToolCallReq] = Field(default_factory=list)
    tool_call_id: str | None = None
    # Opaque provider content blocks for an assistant turn (e.g. Claude thinking
    # blocks that must be replayed unchanged before tool_use). Harness-owned.
    provider_content: list[dict[str, Any]] | None = None


class LLMUsage(BaseModel):
    """Token counts reported by the provider API (not estimated) plus derived cost."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: LLMUsage) -> LLMUsage:
        return LLMUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


class LLMResponse(BaseModel):
    text: str
    tool_calls: list[ToolCallReq] = Field(default_factory=list)
    usage: LLMUsage = Field(default_factory=LLMUsage)
    provider: str
    model: str
    duration_ms: int = 0
    reasoning: str | None = None
    provider_content: list[dict[str, Any]] | None = None

    def as_message(self) -> LLMMessage:
        """The assistant turn to append to the conversation."""
        return LLMMessage(
            role="assistant",
            content=self.text,
            tool_calls=self.tool_calls,
            provider_content=self.provider_content,
        )


class LLMDelta(BaseModel):
    channel: Literal["reasoning", "answer"]
    text: str


class StructuredResult[T: BaseModel](BaseModel):
    value: T | None
    raw_text: str = ""
    mode: StructuredMode
    usage: LLMUsage = Field(default_factory=LLMUsage)
    provider: str | None = None
    model: str | None = None


class LLMPort(Protocol):
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        tier: Tier,
        tools: list[ToolSpec] | None = None,
        purpose: str,
    ) -> LLMResponse: ...

    async def structured[S: BaseModel](
        self, messages: list[LLMMessage], schema: type[S], *, tier: Tier, purpose: str
    ) -> StructuredResult[S]: ...

    def stream(self, messages: list[LLMMessage], *, tier: Tier, purpose: str) -> AsyncIterator[LLMDelta]: ...


class EventPort(Protocol):
    """Emit a run event (contracts.md §10). Returns the event's ``seq``."""

    async def emit(
        self,
        type: str,
        payload: dict[str, Any],
        *,
        node: str | None = None,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> int: ...


class ToolRunnerPort(Protocol):
    """Run tool calls through the harness executor (validate → permission → execute → normalize)."""

    async def run_calls(
        self, calls: list[ToolCallReq], *, parent_call_id: str | None = None
    ) -> list[Any]: ...


class AgentRunRequest(BaseModel):
    """(P07) One pass of the harness agent loop, as asked for by a workflow node."""

    role: str = "research_lead"
    skill: str | None = None  # preload this skill's instructions before the first LLM call
    instruction: str | None = None  # extra user-turn instruction (e.g. the verify pass)
    max_iterations: int | None = None  # None = the run's own limit
    require_data: bool = False  # a data question: nudge once if the model answers without tools
    wrap_up: bool = False  # at max_iterations with no reply yet, force one tool-less call to write it
    # (P17) blocks appended to the system prompt (INVESTOR PROFILE, LESSONS, advice mode) — not the
    # user turn, so micro-compaction never clears them; the agent keeps them for later passes
    context_blocks: list[str] = Field(default_factory=list)


class AgentRunResult(BaseModel):
    iterations: int
    stop_reason: str | None = None  # None = the agent finished on its own
    draft: str = ""  # the agent's last plain-text reply (a brief, not the final answer)
    tool_calls: int = 0


class AnswerCheck(BaseModel):
    markdown: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    unverified_numbers: list[str] = Field(default_factory=list)


class AgentPort(Protocol):
    """(P07) Agent loop + final answer, implemented by the harness for workflow nodes."""

    async def run_loop(self, req: AgentRunRequest) -> AgentRunResult: ...

    async def check(self, markdown: str) -> AnswerCheck: ...

    async def final_answer(
        self,
        *,
        role: str,
        stop_reason: str | None,
        context_blocks: list[str] | None = None,
        closing_question: str | None = None,
        discovery: dict[str, Any] | None = None,
    ) -> AnswerCheck:
        """(P17) ``context_blocks`` go into the system prompt, ``closing_question`` is asked verbatim
        as the answer's single last question, ``discovery`` is added to ``answer.final``."""
        ...
