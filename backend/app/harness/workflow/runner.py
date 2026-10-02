"""``GraphRunner``: run a ``WorkflowSpec`` on LangGraph, with checkpoint/resume and
approval (T3/T4, phase-09 §9.5). Replaces ``SequentialRunner`` as the harness entry
point for a workflow; ``sequential.py`` stays for workflows that need neither.

Two ways back into a paused run, both by ``run_id``:
- crash resume (``resume_value=None``): the process died mid-node: LangGraph
  re-runs whatever node did not finish and continues from there.
- approval resume (``resume_value=<ApprovalResponse dict>``): a node called
  ``request_approval`` and is parked in ``interrupt()``; the value becomes
  that call's return value.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from langgraph.types import Command
from ulid import ULID

from app.domain.workflows.spec import ApprovalRequest, ApprovalResponse, WorkflowSpec
from app.harness.events.bus import EventBus, RunEmitter
from app.harness.graph.checkpoint import CheckpointerCache, graph_signature
from app.harness.graph.checkpoint import thread_id as make_thread_id
from app.harness.graph.compile import NodeContextFactory, compile_workflow
from app.harness.runtime.context import RunContext
from app.harness.storage import approvals_repo, runs_repo
from app.harness.workflow.sequential import ScopedEmitter
from app.harness.workflow.validate import validate_spec

ScopeFn = Any  # str | Callable[[dict[str, Any]], str]
DateFn = Any  # Callable[[str, dict[str, Any]], str]


class ResumeRejected(Exception):
    pass


NodeFinishedHook = Callable[[str, dict[str, Any]], Awaitable[None]]


def _default_date(run_id: str, _input: dict[str, Any]) -> str:
    """No natural "same day = same analysis" concept for an ad-hoc run: key on ``run_id``
    so concurrent runs of the same scope never collide. Symbol-scoped workflows (P10) pass
    the trading day instead, so re-running the same symbol the same day resumes."""
    return run_id


class GraphRunner:
    def __init__(
        self,
        spec: WorkflowSpec,
        *,
        checkpoints: CheckpointerCache,
        scope: str | ScopeFn | None = None,
        date_fn: DateFn = _default_date,
        keep_checkpoints_on_success: bool = True,
        recursion_limit: int = 100,
        on_node_finished: NodeFinishedHook | None = None,
    ) -> None:
        validate_spec(spec)
        self.spec = spec
        self.checkpoints = checkpoints
        self._scope = scope or f"_workflow_{spec.id}"
        self._date_fn = date_fn
        self.keep_checkpoints_on_success = keep_checkpoints_on_success
        self.recursion_limit = recursion_limit
        self.on_node_finished = on_node_finished

    def _scope_of(self, input: dict[str, Any]) -> str:
        return self._scope(input) if callable(self._scope) else self._scope

    async def start(self, ctx: RunContext, node_context_factory: NodeContextFactory) -> dict[str, Any] | None:
        scope = self._scope_of(ctx.input)
        sig = graph_signature(self.spec, ctx.input)
        tid = make_thread_id(scope, self._date_fn(ctx.run_id, ctx.input), sig)
        await runs_repo.set_status(ctx.run_id, "running", thread_id=tid)
        # A fresh start is fresh: a symbol-scoped thread (same symbol, same day) may still hold
        # an older run's checkpoint. Resuming that run is explicit, by its own run_id.
        await self.checkpoints.clear_checkpoint(scope, tid)
        initial = {"input": self.spec.input_model.model_validate(ctx.input).model_dump(mode="json")}
        return await self._run(ctx, node_context_factory, scope, tid, initial)

    async def resume(
        self, ctx: RunContext, node_context_factory: NodeContextFactory, *, resume_value: Any = None
    ) -> dict[str, Any] | None:
        record = await runs_repo.get_run(ctx.run_id)
        if record is None or record.thread_id is None:
            raise ResumeRejected(f"run {ctx.run_id} has no checkpoint to resume from")
        if resume_value is None and record.status == "waiting_approval":
            raise ResumeRejected(f"run {ctx.run_id} is waiting for an approval, not a crash resume")
        scope = self._scope_of(record.input)
        sig = graph_signature(self.spec, record.input)
        expected = make_thread_id(scope, self._date_fn(ctx.run_id, record.input), sig)
        if expected != record.thread_id:
            raise ResumeRejected(f"run {ctx.run_id}: graph shape changed since it started; cannot resume")
        await runs_repo.set_status(ctx.run_id, "running")
        stream_input: Any = None if resume_value is None else Command(resume=resume_value)
        return await self._run(ctx, node_context_factory, scope, record.thread_id, stream_input)

    async def _run(
        self,
        ctx: RunContext,
        node_context_factory: NodeContextFactory,
        scope: str,
        tid: str,
        stream_input: Any,
    ) -> dict[str, Any] | None:
        checkpointer = await self.checkpoints.get(scope)
        # Reuse the caller's own ScopedEmitter (e.g. research.py binds its LLM/executor to
        # it once for the whole run) instead of wrapping it again — see compile.py's note
        # on why node tagging mutates ``.node`` on one shared emitter.
        emitter = ctx.emit if isinstance(ctx.emit, ScopedEmitter) else ScopedEmitter(ctx.emit)
        compiled = compile_workflow(
            self.spec,
            checkpointer=checkpointer,
            node_context_factory=node_context_factory,
            emit=emitter,
            thread_id=tid,
        )
        config = {"configurable": {"thread_id": tid}, "recursion_limit": self.recursion_limit}
        async for update in compiled.graph.astream(stream_input, config, stream_mode="updates"):
            if "__interrupt__" in update:
                await self._handle_interrupt(compiled, config, update["__interrupt__"], ctx, tid)
                return None
            if self.on_node_finished is not None:
                (node_id,) = update.keys()
                snapshot = await compiled.graph.aget_state(config)
                await self.on_node_finished(node_id, dict(snapshot.values))
        snapshot = await compiled.graph.aget_state(config)
        if not self.keep_checkpoints_on_success:
            await self.checkpoints.clear_checkpoint(scope, tid)
        return dict(snapshot.values)

    async def _handle_interrupt(
        self, compiled: Any, config: dict[str, Any], interrupts: tuple[Any, ...], ctx: RunContext, tid: str
    ) -> None:
        interrupt_obj = interrupts[0]
        snapshot = await compiled.graph.aget_state(config)
        node_id = next((task.name for task in snapshot.tasks if task.interrupts), None)
        req = ApprovalRequest.model_validate(interrupt_obj.value)
        approval_id = f"apr_{ULID()}"
        await approvals_repo.create_approval(
            approval_id, ctx.run_id, node=node_id, kind=req.kind, request=req.model_dump(mode="json")
        )
        await ctx.emit.emit(
            "approval.required",
            {
                "approval_id": approval_id,
                "kind": req.kind,
                "title": req.title,
                "proposal": req.proposal,
                "policy_results": req.policy_results,
                "provenance_ids": req.provenance_ids,
            },
            node=node_id,
        )
        await runs_repo.set_status(ctx.run_id, "waiting_approval", thread_id=tid)
        ctx.outcome_status = "waiting_approval"


class ApprovalNotPending(Exception):
    pass


async def answer_approval(
    *, bus: EventBus, run_manager: Any, approval_id: str, response: ApprovalResponse
) -> approvals_repo.ApprovalRecord:
    """Resolve a pending approval and resume its run with the response (phase-09 §9.5/§9.7).

    ``run_manager`` is a ``RunManager`` (typed ``Any`` to avoid a harness.runtime <->
    harness.workflow import cycle: it is only ever duck-called with ``.resume(...)``).
    """
    approval = await approvals_repo.get_approval(approval_id)
    if approval is None:
        raise KeyError(approval_id)
    if approval.status != "pending":
        raise ApprovalNotPending(f"approval {approval_id} is already {approval.status}")
    resolved = await approvals_repo.resolve_approval(
        approval_id, outcome=response.outcome, response=response.model_dump(mode="json")
    )
    assert resolved is not None
    await RunEmitter(bus, approval.run_id).emit(
        "approval.resolved",
        {"approval_id": approval_id, "outcome": response.outcome, "edits": response.edits},
        node=approval.node,
    )
    await run_manager.resume(approval.run_id, resume_value=response.model_dump(mode="json"))
    return resolved


def utcnow_date() -> str:
    return datetime.now(UTC).date().isoformat()


class _NullEmit:
    async def emit(
        self,
        type: str,
        payload: dict[str, Any],
        *,
        node: str | None = None,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> int:
        return 0


def _dummy_node_context_factory(node: Any, emit: Any) -> Any:
    return None


async def graph_state_snapshot(
    spec: WorkflowSpec, checkpoints: CheckpointerCache, *, scope: str, thread_id: str
) -> Any:
    """Read-only ``StateSnapshot`` for ``GET /runs/{id}/state`` (phase-09 §9.7):
    no node ever runs here, so a null emit/node-context is enough."""
    checkpointer = await checkpoints.get(scope)
    compiled = compile_workflow(
        spec,
        checkpointer=checkpointer,
        node_context_factory=_dummy_node_context_factory,
        emit=ScopedEmitter(_NullEmit()),
        thread_id=thread_id,
    )
    config = {"configurable": {"thread_id": thread_id}}
    return await compiled.graph.aget_state(config)
