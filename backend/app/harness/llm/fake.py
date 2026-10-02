"""FakeLLM: a scripted ``LLMPort`` for tests of every later phase.

Each call pops the next ``FakeTurn`` from the script and records what was
asked (method, tier, purpose, messages, tools, schema) so tests can
assert on it. Usage is fake but controllable (``input_tokens``) so P06
compaction thresholds can be driven deterministically.

Example::

    from app.domain.models.decision import Decision, Verdict
    from app.harness.llm.fake import FakeLLM, FakeTurn

    llm = FakeLLM([
        FakeTurn(tool_calls=[ToolCallReq(id="c1", name="get_quote", args={"symbol": "PTT"})]),
        FakeTurn(text="PTT closed at 34.50 THB", input_tokens=90_000),
        Decision(verdict=Verdict.APPROVE, confidence=0.9, reason="ok"),   # structured value
        TimeoutError("boom"),                                             # raised by complete()
    ])
    resp = await llm.complete(msgs, tier="quick", purpose="agent")
    assert llm.calls[0].purpose == "agent"

A structured call whose turn is an exception, or text that does not parse,
returns ``mode="failed"`` (same contract as ``HarnessLLM.structured``).

``bind(emit=, on_usage=, node=)`` mirrors ``HarnessLLM.bind``: the bound copy
shares the script and call log, emits ``llm.started`` / ``llm.finished`` and
reports usage per purpose, so run-level tests see the real event sequence.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from app.domain.ports import (
    EventPort,
    LLMDelta,
    LLMMessage,
    LLMResponse,
    LLMUsage,
    StructuredResult,
    Tier,
    ToolCallReq,
)
from app.domain.tools.base import ToolSpec
from app.harness.structured.invoke import parse_structured


@dataclass
class FakeTurn:
    text: str = ""
    tool_calls: list[ToolCallReq] = field(default_factory=list)
    structured: BaseModel | dict[str, Any] | None = None
    error: BaseException | None = None
    reasoning: str | None = None
    input_tokens: int = 100
    output_tokens: int = 20
    cost_usd: float = 0.0


@dataclass
class FakeCall:
    method: Literal["complete", "structured", "stream"]
    tier: Tier
    purpose: str
    messages: list[LLMMessage]
    tools: list[str] = field(default_factory=list)
    schema: str | None = None


ScriptItem = FakeTurn | str | BaseModel | BaseException


def _as_turn(item: ScriptItem) -> FakeTurn:
    if isinstance(item, FakeTurn):
        return item
    if isinstance(item, str):
        return FakeTurn(text=item)
    if isinstance(item, BaseException):
        return FakeTurn(error=item)
    return FakeTurn(structured=item, text=item.model_dump_json())


class FakeLLM:
    provider = "fake"
    model = "fake-model"

    def __init__(self, script: list[ScriptItem] | None = None, *, default: ScriptItem | None = None) -> None:
        self.script: list[FakeTurn] = [_as_turn(i) for i in script or []]
        self.default = _as_turn(default) if default is not None else None
        self.calls: list[FakeCall] = []
        self._emit_port: EventPort | None = None
        self._on_usage: Callable[[LLMUsage, str], None] | None = None
        self._node: str | None = None

    def push(self, *items: ScriptItem) -> None:
        self.script.extend(_as_turn(i) for i in items)

    def bind(
        self,
        *,
        emit: EventPort | None = None,
        on_usage: Callable[[LLMUsage, str], None] | None = None,
        on_warning: object = None,
        node: str | None = None,
    ) -> FakeLLM:
        bound = type(self).__new__(type(self))
        bound.__dict__.update(self.__dict__)  # share script + call log (same list objects)
        bound._emit_port = emit or self._emit_port
        bound._on_usage = on_usage or self._on_usage
        bound._node = node or self._node
        return bound

    def primary(self, tier: Tier) -> tuple[str, str]:
        return self.provider, self.model

    async def _started(self, tier: Tier, purpose: str) -> None:
        if self._emit_port is not None:
            payload = {"provider": self.provider, "model": self.model, "tier": tier, "purpose": purpose}
            await self._emit_port.emit("llm.started", payload, node=self._node)

    async def _finished(self, usage: LLMUsage, purpose: str, structured_mode: str | None = None) -> None:
        if self._on_usage is not None:
            self._on_usage(usage, purpose)
        if self._emit_port is not None:
            payload: dict[str, Any] = {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cost_usd": usage.cost_usd,
                "duration_ms": 0,
            }
            if structured_mode is not None:
                payload["structured_mode"] = structured_mode
            await self._emit_port.emit("llm.finished", payload, node=self._node)

    def _next(self) -> FakeTurn:
        if self.script:
            return self.script.pop(0)
        if self.default is not None:
            return self.default
        raise AssertionError(f"FakeLLM script exhausted after {len(self.calls)} calls")

    @staticmethod
    def _usage(turn: FakeTurn) -> LLMUsage:
        return LLMUsage(
            input_tokens=turn.input_tokens, output_tokens=turn.output_tokens, cost_usd=turn.cost_usd
        )

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        tier: Tier,
        tools: list[ToolSpec] | None = None,
        purpose: str,
    ) -> LLMResponse:
        self.calls.append(FakeCall("complete", tier, purpose, list(messages), [t.name for t in tools or []]))
        await self._started(tier, purpose)
        turn = self._next()
        if turn.error is not None:
            raise turn.error
        usage = self._usage(turn)
        await self._finished(usage, purpose)
        return LLMResponse(
            text=turn.text,
            tool_calls=list(turn.tool_calls),
            usage=usage,
            provider=self.provider,
            model=self.model,
            reasoning=turn.reasoning,
        )

    async def structured[S: BaseModel](
        self, messages: list[LLMMessage], schema: type[S], *, tier: Tier, purpose: str
    ) -> StructuredResult[S]:
        self.calls.append(FakeCall("structured", tier, purpose, list(messages), schema=schema.__name__))
        await self._started(tier, purpose)
        result = self._structured(schema)
        await self._finished(result.usage, purpose, structured_mode=result.mode)
        return result

    def _structured[S: BaseModel](self, schema: type[S]) -> StructuredResult[S]:
        turn = self._next()
        usage = self._usage(turn)
        if turn.error is not None:
            return StructuredResult[S](value=None, raw_text=str(turn.error), mode="failed", usage=usage)
        if turn.structured is not None:
            try:
                value = schema.model_validate(
                    turn.structured.model_dump()
                    if isinstance(turn.structured, BaseModel)
                    else turn.structured
                )
            except ValidationError:
                return StructuredResult[S](value=None, raw_text=turn.text, mode="failed", usage=usage)
            return StructuredResult[S](
                value=value,
                raw_text=turn.text,
                mode="native",
                usage=usage,
                provider=self.provider,
                model=self.model,
            )
        parsed = parse_structured(turn.text, schema)
        return StructuredResult[S](
            value=parsed,
            raw_text=turn.text,
            mode="fallback_parsed" if parsed is not None else "failed",
            usage=usage,
            provider=self.provider,
            model=self.model,
        )

    async def stream(
        self, messages: list[LLMMessage], *, tier: Tier, purpose: str
    ) -> AsyncIterator[LLMDelta]:
        self.calls.append(FakeCall("stream", tier, purpose, list(messages)))
        await self._started(tier, purpose)
        turn = self._next()
        if turn.error is not None:
            raise turn.error
        if turn.reasoning:
            yield LLMDelta(channel="reasoning", text=turn.reasoning)
        words = turn.text.split(" ")
        for i, word in enumerate(words):
            yield LLMDelta(channel="answer", text=word if i == len(words) - 1 else word + " ")
        await self._finished(self._usage(turn), purpose)
