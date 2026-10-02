from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, SystemMessage
from pydantic import BaseModel

from app.domain.models.decision import Decision, Verdict
from app.domain.ports import LLMMessage, LLMUsage, ToolCallReq
from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec
from app.harness.llm.adapter import HarnessLLM
from app.harness.llm.failover import FailoverLLM
from app.harness.llm.fake import FakeLLM, FakeTurn
from app.harness.llm.messages import to_langchain, tool_schema
from app.harness.llm.model_catalog import ModelInfo, cost_usd, lookup
from app.harness.llm.usage import extract_usage
from tests.harness.llm.stubs import fake_client


class _Emit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, type: str, payload: dict[str, Any], **_: Any) -> int:
        self.events.append((type, payload))
        return len(self.events)


def _llm(responses: list[AIMessage]) -> HarnessLLM:
    client = fake_client(responses)
    chain = FailoverLLM([client.cfg], factory=lambda cfg: client)
    return HarnessLLM({"deep": chain, "quick": chain})


async def test_complete_reports_usage_cost_and_events() -> None:
    msg = AIMessage(
        content=[{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "สวัสดีครับ"}],
        usage_metadata={
            "input_tokens": 1000,
            "output_tokens": 100,
            "total_tokens": 1100,
            "input_token_details": {"cache_read": 400},
        },
    )
    emit = _Emit()
    usages: list[LLMUsage] = []
    llm = _llm([msg]).bind(emit=emit, on_usage=lambda u, _p: usages.append(u))

    resp = await llm.complete([LLMMessage(role="user", content="hi")], tier="quick", purpose="agent")

    assert resp.text == "สวัสดีครับ"
    assert resp.reasoning == "hmm"
    assert resp.provider_content is not None  # thinking blocks kept for replay
    assert resp.usage.input_tokens == 1000 and resp.usage.cache_read_tokens == 400
    # 600 uncached * $2 + 400 cached * $0.2 + 100 out * $10, per 1M
    assert resp.usage.cost_usd == pytest.approx((600 * 2 + 400 * 0.2 + 100 * 10) / 1_000_000)
    assert [t for t, _ in emit.events] == ["llm.started", "llm.finished"]
    assert emit.events[0][1] == {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "tier": "quick",
        "purpose": "agent",
    }
    assert usages == [resp.usage]


async def test_complete_returns_tool_calls() -> None:
    msg = AIMessage(content="", tool_calls=[{"id": "t1", "name": "get_quote", "args": {"symbol": "PTT"}}])
    resp = await _llm([msg]).complete([LLMMessage(role="user", content="q")], tier="deep", purpose="agent")
    assert resp.tool_calls == [ToolCallReq(id="t1", name="get_quote", args={"symbol": "PTT"})]


def test_message_conversion_roundtrip() -> None:
    lc = to_langchain(
        [
            LLMMessage(role="system", content="sys"),
            LLMMessage(role="user", content="u"),
            LLMMessage(
                role="assistant", content="", tool_calls=[ToolCallReq(id="c1", name="t", args={"a": 1})]
            ),
            LLMMessage(role="tool", content="result", tool_call_id="c1"),
        ]
    )
    assert [m.type for m in lc] == ["system", "human", "ai", "tool"]
    assert lc[2].tool_calls[0]["id"] == "c1"  # type: ignore[attr-defined]


def test_anthropic_system_prompt_gets_cache_control() -> None:
    client = fake_client([])
    prepared = client.prepare([SystemMessage(content="big system prompt")])
    assert prepared[0].content[0]["cache_control"] == {"type": "ephemeral"}  # type: ignore[index]
    other = fake_client([], provider="openai", model="gpt-x")
    assert other.prepare([SystemMessage(content="s")])[0].content == "s"


class _Args(BaseModel):
    symbol: str


async def _noop(args: Any, ctx: ToolContext) -> ToolOutput:
    return ToolOutput(data=None, summary="")


def test_tool_schema_from_args_model() -> None:
    spec = ToolSpec(name="get_quote", description="quote", args_model=_Args, category="data", fn=_noop)
    schema = tool_schema(spec)
    assert schema["function"]["name"] == "get_quote"
    assert schema["function"]["parameters"]["required"] == ["symbol"]


def test_usage_fallback_to_response_metadata() -> None:
    msg = AIMessage(
        content="x", response_metadata={"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}}
    )
    usage = extract_usage(msg)
    assert (usage.input_tokens, usage.output_tokens) == (7, 3)


def test_catalog_lookup() -> None:
    assert lookup("claude-haiku-4-5-20251001").context_window == 200_000
    assert lookup("claude-opus-5-5").input_per_m == 4.0
    unknown = lookup("some-new-model")
    assert unknown.context_window == 128_000 and unknown.input_per_m == 0 and not unknown.known
    assert lookup("llama3.1", free=True).known
    assert (
        cost_usd(ModelInfo(input_per_m=1, output_per_m=2), LLMUsage(input_tokens=10**6, output_tokens=10**6))
        == 3
    )


async def test_fake_llm_script_and_call_capture() -> None:
    llm = FakeLLM(
        [
            FakeTurn(
                tool_calls=[ToolCallReq(id="c1", name="get_quote", args={"symbol": "PTT"})],
                input_tokens=90_000,
            ),
            "final answer",
            Decision(verdict=Verdict.APPROVE, confidence=0.9, reason="ok"),
            "not json",
            TimeoutError("boom"),
        ]
    )
    first = await llm.complete([LLMMessage(role="user", content="q")], tier="deep", purpose="agent")
    assert first.tool_calls[0].name == "get_quote" and first.usage.input_tokens == 90_000
    assert (await llm.complete([], tier="quick", purpose="final")).text == "final answer"
    ok = await llm.structured([], Decision, tier="deep", purpose="structured")
    assert ok.mode == "native" and ok.value is not None
    bad = await llm.structured([], Decision, tier="deep", purpose="structured")
    assert bad.mode == "failed" and bad.value is None
    with pytest.raises(TimeoutError):
        await llm.complete([], tier="quick", purpose="agent")
    assert [(c.method, c.tier, c.purpose) for c in llm.calls][:2] == [
        ("complete", "deep", "agent"),
        ("complete", "quick", "final"),
    ]
    with pytest.raises(AssertionError):
        await llm.complete([], tier="quick", purpose="agent")


async def test_fake_llm_stream() -> None:
    llm = FakeLLM([FakeTurn(text="a b c", reasoning="think")])
    deltas = [d async for d in llm.stream([], tier="quick", purpose="final")]
    assert deltas[0].channel == "reasoning"
    assert "".join(d.text for d in deltas if d.channel == "answer") == "a b c"


async def test_stream_through_adapter() -> None:
    llm = _llm([AIMessage(content="hello world")])
    text = "".join(
        [
            d.text
            async for d in llm.stream([LLMMessage(role="user", content="x")], tier="quick", purpose="final")
        ]
    )
    assert text == "hello world"
