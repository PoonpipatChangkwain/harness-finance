from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.domain.ports import LLMMessage, LLMResponse, LLMUsage, Tier
from app.domain.tools.base import ToolSpec
from app.harness.context.config import ContextConfig
from app.harness.context.manager import MEMORY_FLUSH_TOKEN, ContextManager
from app.harness.context.messages import ToolPairingError
from app.harness.executor.registry import ToolRegistry
from app.harness.llm.fake import FakeCall, FakeLLM
from app.harness.state.memory import MemoryStore
from tests.harness.conftest import RecordingEmitter, make_ctx
from tests.harness.context.helpers import conversation, fill_scratchpad
from tests.harness.fake_tools import all_tools

SUMMARY = "<analysis>a</analysis><summary>1. Original Query: PTT\n6. Numerical Data: 34.5 [prov_X]</summary>"


def registry() -> ToolRegistry:
    return ToolRegistry(all_tools(), capability_available=lambda cap: True)


def manager(llm: Any, **kw: Any) -> ContextManager:
    config = kw.pop("config", ContextConfig(check_pairing=True))
    return ContextManager(llm, registry(), model="fake-model", config=config, **kw)


def events(ctx: Any) -> list[dict[str, Any]]:
    emitter: RecordingEmitter = ctx.emit
    return emitter.events


def of(ctx: Any, type_: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in events(ctx) if e["type"] == type_]


async def test_under_threshold_only_microcompacts(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    msgs = conversation(9)
    mgr = manager(FakeLLM())
    out = await mgr.before_llm_call(ctx, msgs)
    assert len(out) == len(msgs)
    assert of(ctx, "context.microcompact") == [{"cleared": 5, "tokens_saved": 5 * 2}]
    assert of(ctx, "context.compaction") == []
    kinds = [e.get("kind") for e in ctx.scratchpad.entries() if e["type"] == "compaction"]
    assert kinds == ["microcompact"]


async def test_microcompact_discounts_api_token_count(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    ctx.record_llm_usage(LLMUsage(input_tokens=90_000))
    msgs = conversation(9, content="x" * 3500)  # 1000 tokens each
    await manager(FakeLLM()).before_llm_call(ctx, msgs)
    assert ctx.last_api_input_tokens == 85_000


async def test_over_threshold_compacts_to_system_plus_summary(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    ctx.query = "วิเคราะห์ PTT"
    msgs = conversation(4)
    fill_scratchpad(ctx, msgs)
    ctx.record_llm_usage(LLMUsage(input_tokens=180_000))
    llm = FakeLLM([SUMMARY])
    mgr = manager(llm)

    out = await mgr.before_llm_call(ctx, msgs)

    assert [m.role for m in out] == ["system", "user"]
    assert out[0] == msgs[0]
    assert out[1].content.startswith("วิเคราะห์ PTT\n\n<context_summary>")
    assert "34.5 [prov_X]" in out[1].content
    phases = [p["phase"] for p in of(ctx, "context.compaction")]
    assert phases == ["start", "end"]
    end = of(ctx, "context.compaction")[1]
    assert end["pre_tokens"] == 180_000 and 0 < end["post_tokens"] < 1000 and "prov_X" in end["summary"]
    # full tool results are still in the scratchpad, untouched
    results = ctx.scratchpad.tool_results()
    assert len(results) == 4 and all(r["result"]["full"] == "result" for r in results)
    assert ctx.scratchpad.active_tool_result_count() == 0
    assert ctx.scratchpad.compaction_summary is not None
    assert ctx.last_api_input_tokens is None
    assert llm.calls[0].purpose == "compact" and llm.calls[0].tools == []


async def test_three_failures_then_no_more_compaction_attempts(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    msgs = conversation(8)
    fill_scratchpad(ctx, msgs)
    llm = FakeLLM([RuntimeError("down"), RuntimeError("down"), RuntimeError("down")])
    mgr = manager(llm)

    for _ in range(4):
        ctx.record_llm_usage(LLMUsage(input_tokens=180_000))
        msgs = await mgr.before_llm_call(ctx, msgs)

    assert len(llm.calls) == 3
    phases = [p["phase"] for p in of(ctx, "context.compaction")]
    assert phases.count("start") == 3 and phases.count("failed") == 3
    assert phases[-1] == "fallback_clear" or "fallback_clear" in phases
    fallback = next(p for p in of(ctx, "context.compaction") if p["phase"] == "fallback_clear")
    assert fallback["cleared"] == 6 and fallback["kept"] == 5
    assert mgr.failures == 3


async def test_success_resets_failure_count(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    msgs = conversation(4)
    fill_scratchpad(ctx, msgs)
    mgr = manager(FakeLLM([RuntimeError("x"), SUMMARY]))
    ctx.record_llm_usage(LLMUsage(input_tokens=180_000))
    msgs = await mgr.before_llm_call(ctx, msgs)
    assert mgr.failures == 1
    ctx.record_llm_usage(LLMUsage(input_tokens=180_000))
    out = await mgr.before_llm_call(ctx, msgs)
    assert mgr.failures == 0 and [m.role for m in out] == ["system", "user"]


async def test_few_active_results_skip_to_truncate(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    msgs = conversation(8)
    fill_scratchpad(ctx, msgs[:6])  # only 2 tool results in scratchpad
    llm = FakeLLM()
    ctx.record_llm_usage(LLMUsage(input_tokens=180_000))
    out = await manager(llm).before_llm_call(ctx, msgs)
    assert llm.calls == []
    assert [p["phase"] for p in of(ctx, "context.compaction")] == ["fallback_clear"]
    assert len(out) == len(msgs) - 6


async def test_fallback_with_nothing_to_remove_returns_messages(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    msgs = conversation(1)
    ctx.record_llm_usage(LLMUsage(input_tokens=180_000))
    out = await manager(FakeLLM()).before_llm_call(ctx, msgs)
    assert out == msgs and of(ctx, "context.compaction") == []


class EchoLLM(FakeLLM):
    """Summarizer that echoes its input inside <summary> (so ids must come from the scratchpad)."""

    async def complete(
        self, messages: list[LLMMessage], *, tier: Tier, tools: Any = None, purpose: str
    ) -> LLMResponse:
        self.calls.append(FakeCall("complete", tier, purpose, list(messages)))
        body = messages[-1].content.split("Data retrieved from tool calls:")[-1]
        return LLMResponse(text=f"<summary>{body}</summary>", provider="echo", model="echo")


async def test_summary_carries_scratchpad_provenance_ids(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    msgs = conversation(4, content="[Old tool result content cleared — full result in scratchpad call_id=x]")
    fill_scratchpad(ctx, msgs)
    ctx.record_llm_usage(LLMUsage(input_tokens=180_000))
    out = await manager(EchoLLM()).before_llm_call(ctx, msgs)
    summary = out[1].content
    ids = {pid for r in ctx.scratchpad.tool_results() for pid in r["provenance_ids"]}
    assert ids and ids <= set(re.findall(r"prov_[A-Z0-9]+", summary))
    assert "34.5" in summary  # the full result came from the scratchpad, not the cleared messages


async def test_second_compaction_reads_previous_summary_plus_new_results(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    msgs = conversation(3)
    fill_scratchpad(ctx, msgs)
    llm = FakeLLM([SUMMARY, SUMMARY])
    mgr = manager(llm)
    ctx.record_llm_usage(LLMUsage(input_tokens=180_000))
    msgs = await mgr.before_llm_call(ctx, msgs)
    more = conversation(3)[2:]
    more = [
        m.model_copy(update={"tool_call_id": f"n{m.tool_call_id}"}) if m.role == "tool" else m for m in more
    ]
    more = [
        m.model_copy(update={"tool_calls": [c.model_copy(update={"id": f"n{c.id}"}) for c in m.tool_calls]})
        for m in more
    ]
    msgs = msgs + more
    fill_scratchpad(ctx, more)
    ctx.record_llm_usage(LLMUsage(input_tokens=180_000))
    await mgr.before_llm_call(ctx, msgs)
    second_input = llm.calls[1].messages[-1].content
    assert "This session is being continued" in second_input and "ncall_0_0" in second_input


async def test_memory_flush_writes_notes_once(tmp_path: Path, db: None) -> None:
    ctx = make_ctx(tmp_path)
    msgs = conversation(4)
    fill_scratchpad(ctx, msgs)
    memory = MemoryStore()
    llm = FakeLLM(["- ผู้ใช้รับความเสี่ยงได้ปานกลาง", SUMMARY])
    mgr = manager(llm, memory=memory)
    ctx.record_llm_usage(LLMUsage(input_tokens=180_000))
    await mgr.before_llm_call(ctx, msgs)
    item = await memory.get("research_notes", ctx.run_id)
    assert item is not None and "ความเสี่ยง" in item.value["notes"]
    assert [c.purpose for c in llm.calls] == ["compact", "compact"]


async def test_memory_flush_nothing_to_store_or_error_is_skipped(tmp_path: Path, db: None) -> None:
    memory = MemoryStore()
    for script in ([MEMORY_FLUSH_TOKEN, SUMMARY], [RuntimeError("flush down"), SUMMARY]):
        ctx = make_ctx(tmp_path, run_id=f"run_{len(script[0]) if isinstance(script[0], str) else 'E'}")
        msgs = conversation(4)
        fill_scratchpad(ctx, msgs)
        ctx.record_llm_usage(LLMUsage(input_tokens=180_000))
        out = await manager(FakeLLM(script), memory=memory).before_llm_call(ctx, msgs)
        assert [m.role for m in out] == ["system", "user"]
    assert await memory.search("research_notes") == []


async def test_report_usage_emits_context_usage(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    ctx.record_llm_usage(LLMUsage(input_tokens=64_000))
    used = await manager(FakeLLM()).report_usage(ctx, [])
    assert used == 64_000
    assert of(ctx, "context.usage") == [{"used_tokens": 64_000, "context_window": 128_000, "percent": 50.0}]


async def test_check_pairing_raises_on_broken_history(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    broken = conversation(1)[:-1]  # assistant call without its result
    with pytest.raises(ToolPairingError):
        await manager(FakeLLM()).before_llm_call(ctx, broken)
    # off by default
    out = await ContextManager(FakeLLM(), registry(), model="fake-model").before_llm_call(ctx, broken)
    assert out == broken


def test_summary_input_budget_uses_summary_model() -> None:
    tools: list[ToolSpec] = []
    big = ContextManager(FakeLLM(), ToolRegistry(tools), model="fake-model", summary_model="claude-haiku-4-5")
    small = ContextManager(FakeLLM(), ToolRegistry(tools), model="fake-model")
    assert big.summary_input_chars > small.summary_input_chars
