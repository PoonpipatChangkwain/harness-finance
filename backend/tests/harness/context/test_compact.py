from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.ports import LLMMessage, ToolCallReq
from app.harness.context.compact import (
    CompactionFailed,
    build_compact_summary_message,
    build_compaction_prompt,
    compact_context,
    compact_messages,
    fit_text,
    format_compact_summary,
)
from app.harness.context.messages import (
    ToolPairingError,
    assert_tool_pairing,
    truncate_messages,
)
from app.harness.llm.fake import FakeLLM, FakeTurn
from tests.harness.context.helpers import conversation, round_, system, user

SNAPSHOT = Path(__file__).parent / "snapshots" / "compaction_prompt.txt"


def test_compaction_prompt_snapshot() -> None:
    """The prompt must not change by accident; update the snapshot file on purpose."""
    prompt = build_compaction_prompt("{QUERY}", "{TOOL_RESULTS}")
    assert prompt == SNAPSHOT.read_text(encoding="utf-8")
    for section in ("Original Query and Intent", "Numerical Data", "Recommended Next Steps", "prov_"):
        assert section in prompt


def test_format_summary_strips_analysis() -> None:
    raw = "<analysis>draft</analysis>\n<summary>\n1. Query: PTT\n</summary>"
    assert format_compact_summary(raw) == "Summary:\n1. Query: PTT"
    assert format_compact_summary("plain text summary") == "plain text summary"
    framed = build_compact_summary_message(raw)
    assert framed.startswith("This session is being continued") and "1. Query: PTT" in framed


async def test_compact_context_ok_and_uses_quick_tier_without_tools() -> None:
    llm = FakeLLM(["<analysis>x</analysis><summary>S [prov_A]</summary>"])
    result = await compact_context(llm, query="q", tool_results="r")
    assert "S [prov_A]" in result.summary and result.raw_summary.startswith("<analysis>")
    call = llm.calls[0]
    assert (call.tier, call.purpose, call.tools) == ("quick", "compact", [])


async def test_compact_context_rejects_tool_calls_and_empty() -> None:
    llm = FakeLLM([FakeTurn(tool_calls=[ToolCallReq(id="c", name="x")]), "   ", "<analysis>only</analysis>"])
    for _ in range(3):
        with pytest.raises(CompactionFailed):
            await compact_context(llm, query="q", tool_results="r")


def test_compact_messages_keeps_system_only() -> None:
    msgs = conversation(3)
    out = compact_messages(msgs, "SUMMARY", "วิเคราะห์ PTT")
    assert [m.role for m in out] == ["system", "user"]
    assert out[1].content == "วิเคราะห์ PTT\n\n<context_summary>\nSUMMARY\n</context_summary>"
    assert [m.role for m in compact_messages(msgs[1:], "S", "q")] == ["user"]


def test_fit_text_cuts_each_block_evenly() -> None:
    assert fit_text(["a", "b"], 100) == "a\n\nb"
    out = fit_text(["x" * 5000, "y" * 5000], 1000)
    assert out.count("cut for compaction") == 2 and "y" in out


def test_truncate_keeps_whole_rounds_and_at_least_n_tool_uses() -> None:
    msgs = conversation(8)
    result = truncate_messages(msgs, keep_tool_uses=5)
    assert result.removed_messages == 6  # 3 rounds x (assistant + tool)
    assert result.removed_call_ids == ["call_0_0", "call_1_0", "call_2_0"]
    assert result.kept_tool_results == 5
    assert result.messages[:2] == msgs[:2]
    assert_tool_pairing(result.messages)


def test_truncate_multi_call_rounds_and_noop_cases() -> None:
    msgs = [system(), user("q"), *round_(0, n_calls=3), *round_(1, n_calls=3), *round_(2, n_calls=3)]
    result = truncate_messages(msgs, keep_tool_uses=5)
    assert result.kept_tool_results == 6 and len(result.removed_call_ids) == 3
    assert truncate_messages([system(), user("q")]).removed_messages == 0
    assert truncate_messages(conversation(2), keep_tool_uses=5).removed_messages == 0
    # a single huge round is always kept
    assert truncate_messages(conversation(1), keep_tool_uses=0).removed_messages == 0


def test_tool_pairing_errors() -> None:
    call = LLMMessage(role="assistant", content="", tool_calls=[ToolCallReq(id="c1", name="t")])
    with pytest.raises(ToolPairingError):
        assert_tool_pairing([call])
    with pytest.raises(ToolPairingError):
        assert_tool_pairing([LLMMessage(role="tool", content="x", tool_call_id="zz")])
    with pytest.raises(ToolPairingError):
        assert_tool_pairing([call, user("interrupt")])
    with pytest.raises(ToolPairingError):
        assert_tool_pairing([call, call])
    assert_tool_pairing([call, LLMMessage(role="tool", content="x", tool_call_id="c1"), user("ok")])
