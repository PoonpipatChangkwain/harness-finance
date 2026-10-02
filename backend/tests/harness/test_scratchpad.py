from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.ports import LLMMessage
from app.harness.state.conversation import Conversation
from app.harness.state.scratchpad import Scratchpad, jaccard, query_from_args, read_entries, tokenize


def test_jsonl_roundtrip_and_full_results(tmp_path: Path) -> None:
    pad = Scratchpad("run_1", tmp_path, query="วิเคราะห์ PTT")
    pad.add_tool_result(
        "c1", "get_prices", {"symbol": "PTT"}, {"bars": list(range(1000))}, provenance_ids=["prov_1"]
    )
    pad.add_thinking("hmm")
    pad.append("guard", kind="iterations")
    entries = read_entries(tmp_path / "run_1.jsonl")
    assert [e["type"] for e in entries] == ["init", "tool_result", "thinking", "guard"]
    assert entries[0]["content"] == "วิเคราะห์ PTT"
    assert len(entries[1]["result"]["bars"]) == 1000
    assert entries[1]["provenance_ids"] == ["prov_1"]


def test_malformed_line_skipped(tmp_path: Path) -> None:
    pad = Scratchpad("run_2", tmp_path, query="q")
    with pad.path.open("a", encoding="utf-8") as f:
        f.write('{"type": "tool_res\n')
    pad.add_note("after")
    assert [e["type"] for e in pad.entries()] == ["init", "note"]


@pytest.mark.parametrize(
    ("a", "b", "similar"),
    [
        ("PTT latest news", "latest news for PTT", True),
        ("PTT earnings 2025-06-30", "PTT earnings 2024-12-31", True),  # dates don't count
        ("PTT", "AOT", False),
        ("apple revenue growth", "apple revenue growth margin", True),
        ("apple revenue", "microsoft cloud revenue", False),
    ],
)
def test_similarity_threshold(a: str, b: str, similar: bool) -> None:
    assert (jaccard(tokenize(a), tokenize(b)) >= 0.7) is similar


def test_limit_three_calls_and_similar_block(tmp_path: Path) -> None:
    pad = Scratchpad("run_3", tmp_path, query="q")
    assert pad.can_call_tool("search", "PTT dividend policy") == (True, None)
    pad.record_tool_call("search", "PTT dividend policy")
    allowed, warning = pad.can_call_tool("search", "dividend policy of PTT")
    assert not allowed and warning and "similar" in warning
    pad.record_tool_call("search", "AOT passenger numbers")
    allowed, warning = pad.can_call_tool("search", "SCB loan growth")
    assert allowed and warning and "Last allowed" in warning
    pad.record_tool_call("search", "SCB loan growth")
    allowed, _ = pad.can_call_tool("search", "totally different")
    assert not allowed
    assert "search: 3/3" in (pad.tool_usage_status() or "")


def test_counts_rebuilt_from_file(tmp_path: Path) -> None:
    pad = Scratchpad("run_4", tmp_path, query="q")
    pad.add_tool_result("c1", "t", {"q": "alpha beta"}, "r")
    pad.add_tool_result("c2", "t", {"q": "bad"}, "ValidationError", status="error", counted=False)
    again = Scratchpad("run_4", tmp_path)
    assert again.tool_call_count("t") == 1
    assert len(again.entries()) == 3  # init not duplicated


def test_cleared_and_final_context(tmp_path: Path) -> None:
    pad = Scratchpad("run_5", tmp_path, query="q")
    for i in range(3):
        pad.add_tool_result(f"c{i}", "t", {"i": i}, {"value": i}, provenance_ids=[f"prov_{i}"])
    pad.mark_cleared(["c0", "c1"])
    assert pad.active_tool_result_count() == 1
    final = pad.build_final_context()
    assert "call_id=c0" in final and "prov_2" in final  # cleared results still in the final context


def test_query_from_args() -> None:
    assert query_from_args({"query": "x y"}) == "x y"
    assert query_from_args({"symbol": "PTT", "range": "1y", "flag": True}) == "1y PTT"


def test_conversation_is_separate_and_compactable() -> None:
    conv = Conversation([LLMMessage(role="user", content="hi")])
    conv.system("sys")
    conv.add(LLMMessage(role="tool", content="big result", tool_call_id="c1"))
    assert [m.role for m in conv.messages] == ["system", "user", "tool"]
    [(idx, _)] = conv.tool_messages()
    conv.set_content(idx, "[Old tool result content cleared]")
    assert conv.messages[idx].content.startswith("[Old")
    conv.replace([LLMMessage(role="user", content="summary")])
    assert len(conv) == 1
