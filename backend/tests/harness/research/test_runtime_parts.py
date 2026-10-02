"""Sequential runner, permissions, memory tools, final-answer helpers, history (P07)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.domain.ports import AgentRunRequest, ToolCallReq
from app.domain.workflows.spec import NodeKind, NodeSpec, RouterSpec, WorkflowSpec
from app.harness.executor.executor import ToolExecutor
from app.harness.executor.permissions import PermissionPolicy
from app.harness.executor.registry import ToolRegistry
from app.harness.provenance.store import ProvenanceStore
from app.harness.runtime.final_answer import fallback_answer, final_context, is_hollow, stop_note
from app.harness.runtime.research_agent import HistoryItem, history_messages
from app.harness.state.memory import MemoryStore
from app.harness.state.memory_tools import memory_tools
from app.harness.workflow.sequential import ScopedEmitter, SequentialRunner, WorkflowError, apply_update
from tests.harness.conftest import RecordingEmitter, make_ctx


class In(BaseModel):
    x: int = 0


async def _inc(state: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return {"count": 1, "log": f"step{state.get('count', 0)}", "_summary": "inc"}


async def _bad(state: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return {"secret": 1}


async def _boom(state: dict[str, Any], ctx: Any) -> dict[str, Any]:
    raise RuntimeError("node failed")


def _spec(
    nodes: list[NodeSpec], edges: list[tuple[str, str]], routers: list[RouterSpec] | None = None
) -> WorkflowSpec:
    return WorkflowSpec(
        id="t",
        version="1",
        input_model=In,
        output_model=In,
        state_model=dict,
        reducers={"count": "add", "log": "append", "meta": "merge"},
        nodes=nodes,
        edges=edges,
        routers=routers or [],
        entry=nodes[0].id,
    )


def _node(node_id: str, fn: Any, writes: set[str]) -> NodeSpec:
    return NodeSpec(
        id=node_id,
        label=node_id.upper(),
        kind=NodeKind.COMPUTE,
        phase="compute",
        fn=fn,
        writes=frozenset(writes),
    )


async def test_runner_follows_router_and_reducers() -> None:
    emitter = RecordingEmitter()
    scoped = ScopedEmitter(emitter)
    loop_router = RouterSpec(
        source="b", fn=lambda s: "again" if s["count"] < 3 else "done", path_map={"again": "b", "done": "END"}
    )
    spec = _spec(
        [_node("a", _inc, {"count", "log"}), _node("b", _inc, {"count", "log"})], [("a", "b")], [loop_router]
    )
    seen: list[str] = []

    async def hook(node_id: str, state: dict[str, Any]) -> None:
        seen.append(node_id)

    state = await SequentialRunner(spec, on_node_finished=hook).run({"x": 1}, None, scoped)  # type: ignore[arg-type]
    assert state["count"] == 3 and state["log"] == ["step0", "step1", "step2"]
    assert seen == ["a", "b", "b"]
    finished = [e["payload"] for e in emitter.events if e["type"] == "node.finished"]
    assert all(p["status"] == "done" and p["summary"] == "inc" for p in finished)
    assert spec.graph_view().edges[-1] == {"source": "b", "target": "END", "label": "done"}


async def test_runner_rejects_undeclared_writes_and_reports_errors() -> None:
    emitter = RecordingEmitter()
    with pytest.raises(WorkflowError, match="undeclared"):
        await SequentialRunner(_spec([_node("a", _bad, {"x"})], [])).run({}, None, ScopedEmitter(emitter))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="node failed"):
        await SequentialRunner(_spec([_node("a", _boom, set())], [])).run({}, None, ScopedEmitter(emitter))  # type: ignore[arg-type]
    statuses = [e["payload"]["status"] for e in emitter.events if e["type"] == "node.finished"]
    assert statuses == ["error", "error"]


async def test_runner_router_must_return_mapped_value() -> None:
    bad = RouterSpec(source="a", fn=lambda s: "nowhere", path_map={"x": "END"})
    with pytest.raises(WorkflowError, match="not in its path map"):
        await SequentialRunner(_spec([_node("a", _inc, {"count", "log"})], [], [bad])).run(
            {},
            None,
            ScopedEmitter(RecordingEmitter()),  # type: ignore[arg-type]
        )
    with pytest.raises(WorkflowError, match="two plain edges"):
        SequentialRunner(_spec([_node("a", _inc, set()), _node("b", _inc, set())], [("a", "b"), ("a", "a")]))
    with pytest.raises(KeyError):
        _spec([_node("a", _inc, set())], []).node("zz")


def test_apply_update_modes() -> None:
    state: dict[str, Any] = {}
    apply_update(
        state,
        {"meta": {"a": 1}, "log": ["x"], "count": 2, "v": 1},
        {"meta": "merge", "log": "append", "count": "add"},
    )
    apply_update(
        state,
        {"meta": {"b": 2}, "log": "y", "count": 3, "v": 2},
        {"meta": "merge", "log": "append", "count": "add"},
    )
    assert state == {"meta": {"a": 1, "b": 2}, "log": ["x", "y"], "count": 5, "v": 2}


def test_permission_ask_when_portfolio_memory(tmp_path: Path) -> None:
    [_get, _search, update] = memory_tools(MemoryStore())
    policy = PermissionPolicy()
    assert policy.evaluate(update, {"namespace": "portfolio", "key": "k", "value": 1}) == "ask"
    assert policy.evaluate(update, {"namespace": "user_prefs", "key": "k", "value": 1}) == "allow"
    assert (
        PermissionPolicy(rules={"memory_update": "deny"}).evaluate(update, {"namespace": "portfolio"})
        == "deny"
    )


@pytest.mark.usefixtures("db")
async def test_memory_tools_roundtrip_and_portfolio_needs_approval(tmp_path: Path) -> None:
    store = MemoryStore()
    ctx = make_ctx(tmp_path)
    executor = ToolExecutor(ToolRegistry(memory_tools(store)), ctx, provenance=ProvenanceStore())
    results = await executor.run_calls(
        [
            ToolCallReq(
                id="c1",
                name="memory_update",
                args={"namespace": "user_prefs", "key": "risk", "value": "moderate"},
            ),
            ToolCallReq(
                id="c2", name="memory_update", args={"namespace": "portfolio", "key": "p", "value": {}}
            ),
        ]
    )
    assert results[0].ok and results[1].status == "denied"
    got = await executor.run_calls(
        [
            ToolCallReq(id="c3", name="memory_get", args={"namespace": "user_prefs", "key": "risk"}),
            ToolCallReq(id="c4", name="memory_get", args={"namespace": "user_prefs", "key": "none"}),
            ToolCallReq(id="c5", name="memory_search", args={"namespace": "user_prefs", "prefix": "ri"}),
        ]
    )
    assert "moderate" in got[0].content and "not found" in got[1].summary and "1 memories" in got[2].summary


def test_stop_note_and_fallback(tmp_path: Path) -> None:
    assert stop_note(None) == ""
    assert "maximum number of steps" in stop_note("iterations: reached 10 iterations")
    assert "weird" in stop_note("weird")
    ctx = make_ctx(tmp_path)
    ctx.scratchpad.add_tool_result("c1", "get_prices", {}, {"a": 1})
    text = fallback_answer(ctx, "Boom")
    assert "get_prices" in text and "ไม่ใช่คำแนะนำการลงทุน" in text


def test_is_hollow() -> None:
    assert is_hollow("") and is_hollow("  ")
    assert is_hollow("**ข้อมูลนี้ไม่ใช่คำแนะนำการลงทุน**")
    assert is_hollow("---\n**Disclaimer:** This is information, not investment advice.")
    assert not is_hollow("ok")
    assert not is_hollow("AAPL 34.5 [^prov_1] ข้อมูลนี้ไม่ใช่คำแนะนำการลงทุน")


def test_final_context_uses_summary_when_too_long(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    for i in range(4):
        ctx.scratchpad.add_tool_result(f"c{i}", "get_prices", {}, "x" * 5000, provenance_ids=[f"prov_{i}"])
    assert "c3" in final_context(ctx, 100_000)
    ctx.scratchpad.set_compaction_summary("SUMMARY OF EARLIER DATA")
    ctx.scratchpad.add_tool_result("c9", "get_quote", {}, "late result")
    small = final_context(ctx, 3000)
    assert small.startswith("SUMMARY OF EARLIER DATA") and "late result" in small and "c0]" not in small


def test_history_messages_start_with_user_and_are_capped() -> None:
    items = [HistoryItem(role="assistant", content="orphan")]
    items += [HistoryItem(role="user" if i % 2 == 0 else "assistant", content=f"m{i}") for i in range(14)]
    items.append(HistoryItem(role="user", content="  "))
    out = history_messages(items)
    assert out[0].role == "user" and len(out) <= 10 and all(m.content.strip() for m in out)


def test_agent_run_request_default_role() -> None:
    assert AgentRunRequest().role == "research_lead"
