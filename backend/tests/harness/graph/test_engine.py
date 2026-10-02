"""Graph engine tests (T1-T4, phase-09 §9.9): validate_spec, the LangGraph adapter
(write violations, router fallback), checkpoint crash-resume, and the approval gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.domain.policy import MinConfidence
from app.domain.workflows.spec import (
    ApprovalResponse,
    NodeContext,
    NodeKind,
    NodeSpec,
    RouterSpec,
    WorkflowSpec,
)
from app.harness.graph.checkpoint import CheckpointerCache
from app.harness.graph.compile import StateWriteViolation
from app.harness.graph.context import make_node_context_factory
from app.harness.storage import runs_repo
from app.harness.workflow.runner import GraphRunner, ResumeRejected
from app.harness.workflow.validate import WorkflowValidationError, validate_spec
from tests.harness.conftest import make_ctx
from tests.harness.graph.fixtures import (
    LinearIn,
    make_approval_workflow,
    make_bad_write_workflow,
    make_linear_workflow,
    make_router_workflow,
)

pytestmark = pytest.mark.usefixtures("db")


def noop_factory(node: NodeSpec, emit: Any) -> NodeContext:
    return make_node_context_factory(llm=None, tools=None, agent=None, run_id="run_TEST")(node, emit)


async def _seed_run(run_id: str, workflow: str, input: dict[str, Any]) -> None:
    await runs_repo.create_run(run_id, workflow, input)


# -- validate_spec --------------------------------------------------------------------------


def test_duplicate_node_id_rejected() -> None:
    async def fn(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        return {}

    spec = WorkflowSpec(
        id="dup",
        version="1",
        input_model=LinearIn,
        output_model=LinearIn,
        state_model=dict,
        nodes=[
            NodeSpec(id="a", label="A", kind=NodeKind.COMPUTE, phase="p", fn=fn, writes=frozenset()),
            NodeSpec(id="a", label="A2", kind=NodeKind.COMPUTE, phase="p", fn=fn, writes=frozenset()),
        ],
        edges=[],
        entry="a",
    )
    with pytest.raises(WorkflowValidationError, match="duplicate node ids"):
        validate_spec(spec)


def test_unreachable_node_rejected() -> None:
    async def fn(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        return {}

    spec = WorkflowSpec(
        id="unreach",
        version="1",
        input_model=LinearIn,
        output_model=LinearIn,
        state_model=dict,
        nodes=[
            NodeSpec(id="a", label="A", kind=NodeKind.COMPUTE, phase="p", fn=fn, writes=frozenset()),
            NodeSpec(id="b", label="B", kind=NodeKind.COMPUTE, phase="p", fn=fn, writes=frozenset()),
        ],
        edges=[],
        entry="a",
    )
    with pytest.raises(WorkflowValidationError, match="unreachable"):
        validate_spec(spec)


def test_router_missing_fallback_rejected() -> None:
    async def fn(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        return {}

    spec = WorkflowSpec(
        id="nofallback",
        version="1",
        input_model=LinearIn,
        output_model=LinearIn,
        state_model=dict,
        nodes=[
            NodeSpec(id="a", label="A", kind=NodeKind.COMPUTE, phase="p", fn=fn, writes=frozenset()),
            NodeSpec(id="b", label="B", kind=NodeKind.COMPUTE, phase="p", fn=fn, writes=frozenset()),
        ],
        edges=[],
        routers=[RouterSpec(source="a", fn=lambda s: "x", path_map={"x": "b"}, returns=frozenset({"x"}))],
        entry="a",
    )
    with pytest.raises(WorkflowValidationError, match="__fallback__"):
        validate_spec(spec)


def test_router_return_not_covered_rejected() -> None:
    async def fn(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        return {}

    spec = WorkflowSpec(
        id="undercovered",
        version="1",
        input_model=LinearIn,
        output_model=LinearIn,
        state_model=dict,
        nodes=[
            NodeSpec(id="a", label="A", kind=NodeKind.COMPUTE, phase="p", fn=fn, writes=frozenset()),
            NodeSpec(id="b", label="B", kind=NodeKind.COMPUTE, phase="p", fn=fn, writes=frozenset()),
        ],
        edges=[],
        routers=[
            RouterSpec(
                source="a", fn=lambda s: "x", path_map={"__fallback__": "b"}, returns=frozenset({"x", "y"})
            )
        ],
        entry="a",
    )
    with pytest.raises(WorkflowValidationError, match="not in its path_map"):
        validate_spec(spec)


def test_multi_writer_fan_out_without_reducer_rejected() -> None:
    """Two nodes reached by plain (unconditional) edges from the same source can run in the
    same superstep — writing the same field with the default 'replace' reducer must fail."""

    async def fn(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        return {"x": 1}

    spec = WorkflowSpec(
        id="fanout",
        version="1",
        input_model=LinearIn,
        output_model=LinearIn,
        state_model=dict,
        nodes=[
            NodeSpec(id="a", label="A", kind=NodeKind.COMPUTE, phase="p", fn=fn, writes=frozenset()),
            NodeSpec(id="b", label="B", kind=NodeKind.COMPUTE, phase="p", fn=fn, writes=frozenset({"x"})),
            NodeSpec(id="c", label="C", kind=NodeKind.COMPUTE, phase="p", fn=fn, writes=frozenset({"x"})),
        ],
        edges=[("a", "b"), ("a", "c")],
        entry="a",
    )
    with pytest.raises(WorkflowValidationError, match="no reducer"):
        validate_spec(spec)


def test_router_targets_are_mutually_exclusive_not_a_conflict() -> None:
    """Router branches never run together — no reducer is needed even with a shared field."""
    validate_spec(make_router_workflow({"value": "go_b"}))


def test_linear_chain_shared_field_without_reducer_is_fine() -> None:
    validate_spec(make_linear_workflow({}, fail={}))


# -- compile.py: write violation + router fallback -------------------------------------------


async def test_state_write_violation_raises(tmp_path: Path) -> None:
    spec = make_bad_write_workflow()
    ctx = make_ctx(tmp_path, run_id="run_WRITE")
    await _seed_run(ctx.run_id, spec.id, {"x": 0})
    checkpoints = CheckpointerCache(tmp_path)
    runner = GraphRunner(spec, checkpoints=checkpoints)
    with pytest.raises(StateWriteViolation):
        await runner.start(ctx, noop_factory)
    await checkpoints.close_all()


async def test_router_fallback_emits_warning(tmp_path: Path) -> None:
    spec = make_router_workflow({"value": "not_a_real_key"})
    ctx = make_ctx(tmp_path, run_id="run_ROUTER")
    await _seed_run(ctx.run_id, spec.id, {"x": 0})
    checkpoints = CheckpointerCache(tmp_path)
    runner = GraphRunner(spec, checkpoints=checkpoints)
    state = await runner.start(ctx, noop_factory)
    assert state is not None and state["log"] == ["a", "c"]  # fallback routes to c
    events: list[dict[str, Any]] = ctx.emit.events  # type: ignore[attr-defined]
    warnings = [e for e in events if e["type"] == "warning"]
    assert warnings and warnings[0]["payload"]["code"] == "router_fallback"
    await checkpoints.close_all()


# -- checkpoint / crash resume (T3) ------------------------------------------------------------


async def test_crash_then_resume_does_not_rerun_completed_nodes(tmp_path: Path) -> None:
    calls: dict[str, int] = {}
    fail = {"c": True}
    spec = make_linear_workflow(calls, fail=fail)
    ctx = make_ctx(tmp_path, run_id="run_CRASH")
    await _seed_run(ctx.run_id, spec.id, {"x": 0})
    checkpoints = CheckpointerCache(tmp_path)
    runner = GraphRunner(spec, checkpoints=checkpoints)

    with pytest.raises(RuntimeError, match="c boom"):
        await runner.start(ctx, noop_factory)
    assert calls == {"a": 1, "b": 1, "c": 1}

    fail["c"] = False
    state = await runner.resume(ctx, noop_factory)
    assert state is not None and state["x"] == 3
    assert calls == {"a": 1, "b": 1, "c": 2}  # a, b were not re-run; c retried and then succeeded
    await checkpoints.close_all()


async def test_resume_rejected_when_graph_signature_changed(tmp_path: Path) -> None:
    calls: dict[str, int] = {}
    spec = make_approval_workflow()
    ctx = make_ctx(tmp_path, run_id="run_SIGCHANGE")
    ctx.input = {"confidence": 0.9}
    await _seed_run(ctx.run_id, spec.id, ctx.input)
    checkpoints = CheckpointerCache(tmp_path)
    runner = GraphRunner(spec, checkpoints=checkpoints)
    state = await runner.start(ctx, noop_factory)
    assert state is None  # parked at the approval interrupt

    # Simulate the run's stored input changing shape between the pause and the resume attempt
    # (a signature_field value differs → the recomputed thread_id no longer matches).
    from sqlalchemy import update

    from app.harness.storage.db import session_scope
    from app.harness.storage.models import RunRow

    async with session_scope() as s:
        await s.execute(update(RunRow).where(RunRow.id == ctx.run_id).values(input={"confidence": 0.1}))
        await s.commit()

    with pytest.raises(ResumeRejected, match="graph shape changed"):
        await runner.resume(ctx, noop_factory, resume_value=ApprovalResponse(outcome="approved").model_dump())
    await checkpoints.close_all()
    del calls


# -- approval gate (T4) ------------------------------------------------------------------------


async def _start_approval(
    tmp_path: Path, run_id: str, confidence: float
) -> tuple[GraphRunner, Any, CheckpointerCache]:
    spec = make_approval_workflow(min_confidence=0.6, max_retries=1)
    ctx = make_ctx(tmp_path, run_id=run_id)
    ctx.input = {"confidence": confidence}
    await _seed_run(ctx.run_id, spec.id, ctx.input)
    checkpoints = CheckpointerCache(tmp_path)
    runner = GraphRunner(spec, checkpoints=checkpoints)
    return runner, ctx, checkpoints


async def test_approval_required_then_approved_executes(tmp_path: Path) -> None:
    runner, ctx, checkpoints = await _start_approval(tmp_path, "run_APR_OK", confidence=0.9)
    state = await runner.start(ctx, noop_factory)
    assert state is None
    events: list[dict[str, Any]] = ctx.emit.events  # type: ignore[attr-defined]
    required = next(e for e in events if e["type"] == "approval.required")
    assert required["payload"]["kind"] == "trade_proposal"
    run = await runs_repo.get_run(ctx.run_id)
    assert run is not None and run.status == "waiting_approval" and run.thread_id is not None

    final = await runner.resume(
        ctx, noop_factory, resume_value=ApprovalResponse(outcome="approved").model_dump()
    )
    assert final is not None and final["decision"] == "APPROVE"
    assert final["log"][-1] == "executed:APPROVE"
    await checkpoints.close_all()


async def test_approval_rejected_ends_without_executing(tmp_path: Path) -> None:
    runner, ctx, checkpoints = await _start_approval(tmp_path, "run_APR_REJECT", confidence=0.9)
    assert await runner.start(ctx, noop_factory) is None
    final = await runner.resume(
        ctx, noop_factory, resume_value=ApprovalResponse(outcome="rejected").model_dump()
    )
    assert final is not None and final["decision"] == "REJECT"
    assert "executed:REJECT" not in final["log"]
    await checkpoints.close_all()


async def test_approval_edited_rechecks_policy_then_executes(tmp_path: Path) -> None:
    runner, ctx, checkpoints = await _start_approval(tmp_path, "run_APR_EDIT", confidence=0.9)
    assert await runner.start(ctx, noop_factory) is None
    response = ApprovalResponse(outcome="edited", edits={"confidence": 0.95})
    final = await runner.resume(ctx, noop_factory, resume_value=response.model_dump())
    assert final is not None
    assert "recheck:ok" in final["log"] and final["log"][-1] == "executed:EDITED"
    await checkpoints.close_all()


async def test_approval_edited_still_blocked_is_rejected(tmp_path: Path) -> None:
    runner, ctx, checkpoints = await _start_approval(tmp_path, "run_APR_EDIT_BAD", confidence=0.9)
    assert await runner.start(ctx, noop_factory) is None
    response = ApprovalResponse(outcome="edited", edits={"confidence": 0.1})
    final = await runner.resume(ctx, noop_factory, resume_value=response.model_dump())
    assert final is not None
    assert "recheck:blocked" in final["log"] and "rejected" in final["log"]
    assert not any(entry.startswith("executed") for entry in final["log"])
    await checkpoints.close_all()


async def test_policy_block_retries_once_then_rejects(tmp_path: Path) -> None:
    """A proposal that never satisfies the policy loops back to ``propose`` once, then REJECTs
    without ever reaching the approval node (phase-09 §9.9)."""
    runner, ctx, checkpoints = await _start_approval(tmp_path, "run_APR_BLOCK", confidence=0.1)
    final = await runner.start(ctx, noop_factory)
    assert final is not None
    assert final["log"] == ["propose#1", "policy:blocked", "propose#2", "policy:blocked", "rejected"]
    assert "decision" not in final or final.get("decision") != "APPROVE"
    assert not any(e["type"] == "approval.required" for e in ctx.emit.events)  # type: ignore[attr-defined]
    await checkpoints.close_all()


async def test_policy_check_uses_min_confidence_rule_directly() -> None:
    result = MinConfidence(0.6).evaluate({"confidence": 0.9}, {})
    assert result.passed
    result2 = MinConfidence(0.6).evaluate({"confidence": 0.1}, {})
    assert not result2.passed and result2.severity == "block"
