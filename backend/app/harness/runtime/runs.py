"""RunManager: start a workflow as a background task, cancel it, never let it fail silently.

- ``start`` creates the ``runs`` row, emits ``run.started`` and spawns the task
- the task always ends with exactly one ``run.finished`` whose status is
  ``completed`` / ``failed`` (uncaught exception, with the error) /
  ``cancelled`` / ``limited`` (wall clock or a guard)
- ``recover_interrupted`` (server startup) marks runs a previous process left
  ``running`` as ``interrupted`` so P09 can resume them
- (P07) a *graceful* workflow is not killed on cancel: the token is set, the
  workflow wraps up (e.g. a final answer from what it has) and the task is
  hard-cancelled only if it is still running after ``cancel_grace_s``
Pattern from ``../dexter/src/agent/agent.ts`` (run lifecycle) and
``../deepseek-harness/packages/guard/timeout-policy``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ulid import ULID

from app.harness.events.bus import EventBus, RunEmitter
from app.harness.runtime.context import CancelledByUser, RunContext, RunUsage
from app.harness.runtime.limits import LimitExceeded, RunLimits
from app.harness.state.scratchpad import Scratchpad
from app.harness.storage import runs_repo
from app.harness.workflow.runner import ResumeRejected
from app.settings import Settings

logger = logging.getLogger(__name__)

WorkflowFn = Callable[[RunContext], Awaitable[dict[str, Any] | None]]
ResumeFn = Callable[[RunContext, Any], Awaitable[dict[str, Any] | None]]


@dataclass
class _ActiveRun:
    ctx: RunContext
    task: asyncio.Task[None]
    graceful: bool = False


class RunManager:
    def __init__(
        self,
        bus: EventBus,
        settings: Settings,
        *,
        workflows: dict[str, WorkflowFn] | None = None,
        services: Any = None,
    ) -> None:
        self.bus = bus
        self.settings = settings
        self.workflows: dict[str, WorkflowFn] = dict(workflows or {})
        self.resume_fns: dict[str, ResumeFn] = {}
        self.services = services
        self.graceful: set[str] = set()
        self.cancel_grace_s = settings.run_cancel_grace_s
        self._active: dict[str, _ActiveRun] = {}
        self._watchdogs: set[asyncio.Task[None]] = set()

    def register(
        self, workflow_id: str, fn: WorkflowFn, *, graceful: bool = False, resume: ResumeFn | None = None
    ) -> None:
        self.workflows[workflow_id] = fn
        if resume is not None:
            self.resume_fns[workflow_id] = resume
        if graceful:
            self.graceful.add(workflow_id)

    def context(self, run_id: str) -> RunContext | None:
        active = self._active.get(run_id)
        return active.ctx if active else None

    def is_running(self, run_id: str) -> bool:
        return run_id in self._active

    async def start(
        self,
        workflow_id: str,
        input: dict[str, Any],
        *,
        session_id: str | None = None,
        limits: RunLimits | None = None,
        thread_id: str | None = None,
    ) -> str:
        fn = self.workflows.get(workflow_id)
        if fn is None:
            raise KeyError(f"unknown workflow {workflow_id!r}")
        limits = limits or RunLimits.from_settings(self.settings)
        run_id = f"run_{ULID()}"
        await runs_repo.create_run(run_id, workflow_id, input, session_id=session_id, thread_id=thread_id)
        self.bus.open_run(run_id, session_id)
        query = str(input.get("query") or input.get("text") or input)
        ctx = RunContext(
            run_id=run_id,
            workflow=workflow_id,
            input=input,
            emit=RunEmitter(self.bus, run_id),
            scratchpad=Scratchpad(
                run_id,
                self.settings.scratchpad_dir,
                query=query,
                max_calls_per_tool=limits.max_calls_per_tool,
                similarity_threshold=limits.similarity_threshold,
            ),
            limits=limits,
            session_id=session_id,
            query=query,
            services=self.services,
        )
        await ctx.emit.emit("run.started", {"workflow": workflow_id, "input": input})
        task = asyncio.create_task(self._run(ctx, fn), name=run_id)
        self._active[run_id] = _ActiveRun(ctx, task, graceful=workflow_id in self.graceful)
        return run_id

    async def resume(self, run_id: str, *, resume_value: Any = None) -> None:
        """Continue a paused run (P09): ``resume_value=None`` is a crash/interrupted-run
        resume, anything else is an ``ApprovalResponse`` payload for a node's ``interrupt()``."""
        if self.is_running(run_id):
            raise RuntimeError(f"run {run_id} is already active")
        record = await runs_repo.get_run(run_id)
        if record is None:
            raise KeyError(run_id)
        resume_fn = self.resume_fns.get(record.workflow)
        if resume_fn is None:
            raise KeyError(f"workflow {record.workflow!r} does not support resume")
        if record.thread_id is None:
            raise ResumeRejected(f"run {run_id} has no checkpoint to resume from")
        if resume_value is None and record.status == "waiting_approval":
            raise ResumeRejected(f"run {run_id} is waiting for an approval, not a crash resume")
        limits = RunLimits.from_settings(self.settings)
        query = str(record.input.get("query") or record.input.get("text") or record.input)
        ctx = RunContext(
            run_id=run_id,
            workflow=record.workflow,
            input=record.input,
            emit=RunEmitter(self.bus, run_id),
            scratchpad=Scratchpad(
                run_id,
                self.settings.scratchpad_dir,
                query=None,  # the file already exists (append-only); do not re-seed "init"
                max_calls_per_tool=limits.max_calls_per_tool,
                similarity_threshold=limits.similarity_threshold,
            ),
            limits=limits,
            session_id=record.session_id,
            query=query,
            services=self.services,
            # usage accumulates across segments (start → approval pause → resume)
            usage=RunUsage.model_validate(record.usage) if record.usage else RunUsage(),
        )
        self.bus.open_run(run_id, record.session_id)
        task = asyncio.create_task(self._run(ctx, lambda c: resume_fn(c, resume_value)), name=run_id)
        self._active[run_id] = _ActiveRun(ctx, task, graceful=record.workflow in self.graceful)

    async def _run(self, ctx: RunContext, fn: WorkflowFn) -> None:
        status, error, reason = "completed", None, None
        output: dict[str, Any] | None = None
        try:
            output = await asyncio.wait_for(fn(ctx), timeout=ctx.limits.run_wall_clock_s)
        except asyncio.CancelledError:
            status, reason = "cancelled", ctx.cancel.reason or "cancelled"
        except CancelledByUser as exc:
            status, reason = "cancelled", str(exc)
        except TimeoutError:
            status, reason = "limited", f"run exceeded {ctx.limits.run_wall_clock_s:.0f}s wall clock"
            await self._safe_emit(ctx, "guard.limit", {"kind": "timeout", "detail": reason})
        except LimitExceeded as exc:
            status, reason = "limited", exc.hit.detail
        except Exception as exc:
            logger.exception("run %s failed", ctx.run_id)
            status, error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            self._active.pop(ctx.run_id, None)
        if status == "completed" and ctx.outcome_status == "waiting_approval":
            # (P09) The workflow already parked the run via ``runs_repo.set_status``
            # and is not finished: no ``run.finished`` here, the UI keeps polling/SSE.
            try:
                await runs_repo.update_usage(ctx.run_id, ctx.usage.model_dump())
            except Exception:
                logger.exception("could not persist usage of paused run %s", ctx.run_id)
            return
        if status == "completed" and ctx.cancel.cancelled:
            status, reason = "cancelled", ctx.cancel.reason or "cancelled"
        elif status == "completed" and ctx.outcome_status:
            status, reason = ctx.outcome_status, ctx.outcome_reason
        try:
            ctx.scratchpad.append("final", status=status, error=error, reason=reason, output=output)
            await runs_repo.finish_run(
                ctx.run_id, status, output=output, error=error, usage=ctx.usage.model_dump()
            )
        except Exception:
            logger.exception("could not persist end of run %s", ctx.run_id)
        await self._safe_emit(ctx, "run.finished", {"status": status, "error": error, "reason": reason})

    @staticmethod
    async def _safe_emit(ctx: RunContext, type_: str, payload: dict[str, Any]) -> None:
        try:
            await ctx.emit.emit(type_, payload)
        except Exception:
            logger.exception("could not emit %s for run %s", type_, ctx.run_id)

    async def cancel(self, run_id: str, reason: str = "cancelled by user", *, wait: bool = True) -> bool:
        active = self._active.get(run_id)
        if active is None:
            return False
        active.ctx.cancel.cancel(reason)
        if active.graceful:
            watchdog = asyncio.create_task(self._hard_cancel_after(active, self.cancel_grace_s))
            self._watchdogs.add(watchdog)
            watchdog.add_done_callback(self._watchdogs.discard)
        else:
            active.task.cancel()
        if wait:
            await asyncio.gather(active.task, return_exceptions=True)
        return True

    @staticmethod
    async def _hard_cancel_after(active: _ActiveRun, grace_s: float) -> None:
        done, _ = await asyncio.wait({active.task}, timeout=grace_s)
        if not done:
            active.task.cancel()

    async def wait(self, run_id: str) -> None:
        active = self._active.get(run_id)
        if active is not None:
            await asyncio.gather(active.task, return_exceptions=True)

    async def recover_interrupted(self) -> list[str]:
        return await runs_repo.mark_running_as_interrupted()

    async def shutdown(self) -> None:
        for active in list(self._active.values()):
            active.ctx.cancel.cancel("server shutdown")
            active.task.cancel()
        await asyncio.gather(*(a.task for a in list(self._active.values())), return_exceptions=True)
        for watchdog in list(self._watchdogs):
            watchdog.cancel()
