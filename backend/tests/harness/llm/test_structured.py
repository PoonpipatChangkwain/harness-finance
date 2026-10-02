from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.domain.models.decision import Decision, Verdict, route_decision
from app.domain.ports import LLMMessage
from app.harness.llm.adapter import HarnessLLM
from app.harness.llm.config import LLMTierConfig
from app.harness.llm.failover import FailoverLLM
from app.harness.structured.invoke import NO_EXTERNAL_TOOLS, extract_json_object, invoke_structured
from tests.harness.llm.stubs import ScriptedClient, StatusError

MSGS = [HumanMessage(content="decide")]
GOOD = {"verdict": "APPROVE", "confidence": 0.8, "reason": "fine"}


def _usage_msg(text: str) -> AIMessage:
    return AIMessage(
        content=text, usage_metadata={"input_tokens": 50, "output_tokens": 10, "total_tokens": 60}
    )


async def test_native_ok() -> None:
    client = ScriptedClient(
        native={"raw": _usage_msg(""), "parsed": Decision(**GOOD), "parsing_error": None},
        freetext=AssertionError("free text must not be called"),
    )
    result = await invoke_structured(client, MSGS, Decision)
    assert result.mode == "native"
    assert result.value is not None and result.value.verdict == Verdict.APPROVE
    assert result.usage.input_tokens == 50
    assert client.freetext_calls == []


async def test_native_fail_then_fallback_parse_ok() -> None:
    text = 'Here you go:\n```json\n{"verdict": "REJECT", "confidence": "0.9", "reason": "too risky"}\n```'
    client = ScriptedClient(native=ValueError("bad json from model"), freetext=_usage_msg(text))
    result = await invoke_structured(client, MSGS, Decision)
    assert result.mode == "fallback_parsed"
    assert result.value is not None and result.value.verdict == Verdict.REJECT
    # The fallback prompt carries the schema and the no-tools rule.
    last = client.freetext_calls[0][-1].content
    assert NO_EXTERNAL_TOOLS in last and '"verdict"' in last


async def test_parsed_none_falls_back() -> None:
    client = ScriptedClient(
        native={"raw": _usage_msg("I think approve"), "parsed": None, "parsing_error": ValueError("x")},
        freetext=_usage_msg(f"{GOOD}".replace("'", '"')),
    )
    result = await invoke_structured(client, MSGS, Decision)
    assert result.mode == "fallback_parsed"
    assert result.usage.input_tokens == 100  # both calls counted


async def test_unsupported_provider_goes_straight_to_fallback() -> None:
    client = ScriptedClient(
        native=None, freetext=_usage_msg('{"verdict":"REVIEW","confidence":0.5,"reason":"r"}')
    )
    result = await invoke_structured(client, MSGS, Decision)
    assert result.mode == "fallback_parsed"


async def test_both_fail_returns_failed_not_raise() -> None:
    client = ScriptedClient(native=ValueError("nope"), freetext=_usage_msg("I cannot produce JSON, sorry."))
    result = await invoke_structured(client, MSGS, Decision)
    assert result.mode == "failed"
    assert result.value is None
    assert "sorry" in result.raw_text
    assert route_decision(result.value) == Verdict.REVIEW


async def test_provider_error_propagates_for_failover() -> None:
    client = ScriptedClient(native=StatusError(529, "overloaded_error"), freetext=_usage_msg("{}"))
    with pytest.raises(StatusError):
        await invoke_structured(client, MSGS, Decision)


async def test_harness_structured_never_raises_when_every_client_is_down() -> None:
    down = ScriptedClient(native=StatusError(503), freetext=StatusError(503))

    async def no_sleep(_: float) -> None:
        return None

    chain = FailoverLLM(
        [LLMTierConfig(provider="anthropic", model="m")], factory=lambda cfg: down, sleep=no_sleep
    )
    llm = HarnessLLM({"deep": chain, "quick": chain})
    result = await llm.structured(
        [LLMMessage(role="user", content="x")], Decision, tier="deep", purpose="structured"
    )
    assert result.mode == "failed"
    assert result.value is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('prefix {"a": {"b": "}"}} suffix', {"a": {"b": "}"}}),
        ('```json\n{"a": 2}\n```', {"a": 2}),
        ('```json\n{broken}\n``` then {"a": 3}', {"a": 3}),
        ("no json here", None),
        ('{not json} {"ok": true}', {"ok": True}),
    ],
)
def test_extract_json_object(text: str, expected: object) -> None:
    assert extract_json_object(text) == expected


def test_route_decision_fail_safe() -> None:
    assert route_decision(None) == Verdict.REVIEW
    assert route_decision(Decision(verdict=Verdict.APPROVE, confidence=0.3, reason="meh")) == Verdict.REVIEW
    assert route_decision(Decision(verdict=Verdict.APPROVE, confidence=0.9, reason="ok")) == Verdict.APPROVE
    assert route_decision(Decision(verdict=Verdict.REJECT, confidence=0.1, reason="no")) == Verdict.REJECT
