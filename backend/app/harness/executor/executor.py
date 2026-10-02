"""ToolExecutor: the runtime boundary between "the LLM asked" and "the harness ran it" (D3).

Ported from ``../dexter/src/agent/tool-executor.ts`` (``AgentToolExecutor``):

1. partition calls: consecutive read-only calls form one concurrent batch
   (≤ ``max_concurrency``); anything with side effects runs alone, in order
2. per call: ``tool.started`` → validate args → guard (per-tool limit +
   similarity, run budget) → permission → execute (timeout + cancel) →
   normalize → scratchpad (full result) + provenance store → ``tool.finished``
3. every failure becomes a ``ToolResult`` the LLM can read; only
   ``asyncio.CancelledError`` escapes
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import ValidationError
from ulid import ULID

from app.domain.models.provenance import ProvenanceRecord
from app.domain.ports import ToolCallReq
from app.domain.tools.base import ToolOutput, ToolSpec
from app.harness.executor.format import DEFAULT_MAX_CHARS, format_output, to_jsonable, truncate
from app.harness.executor.permissions import PermissionPolicy
from app.harness.executor.registry import ToolRegistry
from app.harness.executor.types import ToolError, ToolResult, ToolResultStatus
from app.harness.provenance.store import ProvenanceStore
from app.harness.runtime.context import RunContext
from app.harness.state.scratchpad import query_from_args

DEFAULT_MAX_CONCURRENCY = 10


class _ToolCtx:
    """``ToolContext`` handed to a running tool."""

    def __init__(self, executor: ToolExecutor, call_id: str) -> None:
        self._executor = executor
        self.run_id = executor.ctx.run_id
        self.call_id = call_id

    async def progress(self, message: str) -> None:
        await self._executor.ctx.emit.emit("tool.progress", {"message": message}, call_id=self.call_id)

    def is_cancelled(self) -> bool:
        return self._executor.ctx.cancel.cancelled

    async def call_tools(self, calls: list[ToolCallReq]) -> list[Any]:
        return await self._executor.run_calls(calls, parent_call_id=self.call_id)


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(args)"
        parts.append(f"{loc}: {err.get('msg')}")
    return "Invalid arguments — fix and retry: " + "; ".join(parts)


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        ctx: RunContext,
        *,
        provenance: ProvenanceStore | None = None,
        policy: PermissionPolicy | None = None,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        self.registry = registry
        self.ctx = ctx
        self.provenance = provenance or ProvenanceStore()
        self.policy = policy or PermissionPolicy()
        self.max_chars = max_chars
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def _repair(self, call: ToolCallReq) -> ToolCallReq:
        name = self.registry.resolve_name(call.name, call.args)
        return call if name == call.name else call.model_copy(update={"name": name})

    # -- batching ---------------------------------------------------------------------

    def _partition(self, calls: list[ToolCallReq]) -> list[tuple[bool, list[ToolCallReq]]]:
        batches: list[tuple[bool, list[ToolCallReq]]] = []
        for call in calls:
            spec = self.registry.get(call.name)
            concurrent = spec is not None and spec.read_only
            if concurrent and batches and batches[-1][0]:
                batches[-1][1].append(call)
            else:
                batches.append((concurrent, [call]))
        return batches

    async def run_calls(
        self, calls: list[ToolCallReq], *, parent_call_id: str | None = None
    ) -> list[ToolResult]:
        results: list[ToolResult] = []
        calls = [self._repair(c) for c in calls]
        for concurrent, batch in self._partition(calls):
            batch_id = f"batch_{ULID()}"
            hit = self.ctx.guard.check_runtime(self.ctx)
            if hit is not None:
                await self.ctx.emit.emit("guard.limit", hit.model_dump())
                for call in batch:
                    results.append(
                        await self._finish_early(
                            call, batch_id, parent_call_id, "limited", "RunLimit", hit.detail, emit_start=True
                        )
                    )
                continue
            if concurrent and len(batch) > 1:
                results.extend(
                    await asyncio.gather(*(self._run_guarded(c, batch_id, parent_call_id) for c in batch))
                )
            else:
                for call in batch:
                    results.append(await self._run_guarded(call, batch_id, parent_call_id))
        return results

    async def _run_guarded(self, call: ToolCallReq, batch_id: str, parent_call_id: str | None) -> ToolResult:
        async with self._semaphore:
            return await self._run_one(call, batch_id, parent_call_id)

    # -- single call ---------------------------------------------------------------------

    async def _emit_started(
        self, call: ToolCallReq, spec: ToolSpec | None, batch_id: str, parent_call_id: str | None
    ) -> None:
        await self.ctx.emit.emit(
            "tool.started",
            {
                "tool": call.name,
                "category": spec.category if spec else "unknown",
                "args": to_jsonable(call.args),
                "read_only": spec.read_only if spec else True,
                "batch_id": batch_id,
            },
            call_id=call.id,
            parent_call_id=parent_call_id,
        )

    async def _finish(self, result: ToolResult, parent_call_id: str | None) -> ToolResult:
        await self.ctx.emit.emit(
            "tool.finished", result.finished_payload(), call_id=result.call_id, parent_call_id=parent_call_id
        )
        return result

    async def _finish_early(
        self,
        call: ToolCallReq,
        batch_id: str,
        parent_call_id: str | None,
        status: ToolResultStatus,
        error_type: str,
        message: str,
        *,
        emit_start: bool = False,
        retryable: bool = False,
    ) -> ToolResult:
        """A call that never executed: validation error, limit, denial."""
        if emit_start:
            await self._emit_started(call, self.registry.get(call.name), batch_id, parent_call_id)
        self.ctx.scratchpad.add_tool_result(
            call.id,
            call.name,
            call.args,
            f"{error_type}: {message}",
            status=status,
            parent_call_id=parent_call_id,
            counted=False,
        )
        result = ToolResult(
            call_id=call.id,
            tool=call.name,
            ok=False,
            status=status,
            summary=message[:200],
            content=f"{error_type}: {message}",
            error=ToolError(type=error_type, message=message, retryable=retryable),
        )
        return await self._finish(result, parent_call_id)

    async def _run_one(self, call: ToolCallReq, batch_id: str, parent_call_id: str | None) -> ToolResult:
        spec = self.registry.get(call.name)
        await self._emit_started(call, spec, batch_id, parent_call_id)
        early = (call, batch_id, parent_call_id)

        if spec is None or not self.registry.is_enabled(spec):
            available = ", ".join(s.name for s in self.registry.enabled())
            return await self._finish_early(
                *early, "error", "UnknownTool", f"tool '{call.name}' is not available. Available: {available}"
            )

        # 1. validate
        try:
            args = spec.args_model.model_validate(call.args)
        except ValidationError as exc:
            return await self._finish_early(*early, "error", "ValidationError", _validation_message(exc))

        # 2. guard: run-wide call budget, then per-tool count + similarity
        if self.ctx.usage.tool_calls >= self.ctx.limits.max_tool_calls_total:
            used, cap = self.ctx.usage.tool_calls, self.ctx.limits.max_tool_calls_total
            detail = f"run already made {used} tool calls (limit {cap})"
            await self.ctx.emit.emit("guard.limit", {"kind": "tool_calls", "detail": detail}, call_id=call.id)
            return await self._finish_early(*early, "limited", "ToolLimit", detail)
        query = query_from_args(call.args)
        limit_key = self.ctx.scoped(spec.limit_key(call.args))
        check = self.ctx.scratchpad.check_tool(limit_key, query, max_calls=spec.max_calls_per_run)
        if not check.allowed:
            await self.ctx.emit.emit(
                "guard.limit",
                {"kind": check.kind or "tool_calls", "detail": check.warning or ""},
                call_id=call.id,
            )
            return await self._finish_early(*early, "limited", "ToolLimit", check.warning or "limit reached")

        # 3. permission
        decision = self.policy.evaluate(spec, call.args, self.ctx)
        if decision == "deny":
            return await self._finish_early(
                *early, "denied", "PermissionDenied", f"'{spec.name}' is not allowed"
            )
        if decision == "ask":
            try:
                approved = await self.policy.request_approval(spec, call.args, self.ctx)
            except NotImplementedError as exc:
                return await self._finish_early(*early, "denied", "ApprovalUnavailable", str(exc))
            if not approved:
                return await self._finish_early(
                    *early, "denied", "PermissionDenied", "the user rejected this call"
                )

        # 4. execute
        self.ctx.usage.tool_calls += 1
        self.ctx.scratchpad.record_tool_call(limit_key, query)
        start = time.monotonic()
        output, status, error = await self._execute(spec, args, call.id)
        duration = int((time.monotonic() - start) * 1000)

        # 5. normalize + record
        return await self._normalize(call, spec, output, status, error, duration, parent_call_id)

    async def _execute(
        self, spec: ToolSpec, args: Any, call_id: str
    ) -> tuple[ToolOutput | None, ToolResultStatus, ToolError | None]:
        if self.ctx.cancel.cancelled:
            return None, "cancelled", ToolError(type="Cancelled", message="run was cancelled")
        remaining = self.ctx.limits.run_wall_clock_s - (time.monotonic() - self.ctx.started_monotonic)
        timeout = max(0.01, min(spec.timeout_s, remaining))
        task = asyncio.ensure_future(spec.fn(args, _ToolCtx(self, call_id)))
        cancel_wait = asyncio.ensure_future(self.ctx.cancel.wait())
        try:
            done, _ = await asyncio.wait(
                {task, cancel_wait}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            task.cancel()
            cancel_wait.cancel()
            raise
        cancel_wait.cancel()
        if task not in done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if cancel_wait in done:
                return None, "cancelled", ToolError(type="Cancelled", message="run was cancelled")
            return (
                None,
                "timeout",
                ToolError(
                    type="Timeout", message=f"'{spec.name}' timed out after {timeout:.1f}s", retryable=True
                ),
            )
        exc = task.exception()
        if exc is not None:
            return (
                None,
                "error",
                ToolError(
                    type=type(exc).__name__,
                    message=str(exc) or type(exc).__name__,
                    retryable=bool(getattr(exc, "retryable", False)),
                ),
            )
        output = task.result()
        if not isinstance(output, ToolOutput):
            output = ToolOutput(data=output, summary=f"{spec.name} returned {type(output).__name__}")
        return output, output.status, None

    async def _normalize(
        self,
        call: ToolCallReq,
        spec: ToolSpec,
        output: ToolOutput | None,
        status: ToolResultStatus,
        error: ToolError | None,
        duration_ms: int,
        parent_call_id: str | None,
    ) -> ToolResult:
        provenance_ids: list[str] = []
        if output is not None:
            text = format_output(output.data)
            if output.status == "unavailable" and output.data in (None, "", [], {}):
                text = f"{spec.name}: data unavailable — {output.summary}"
            content, truncated = truncate(text, self.max_chars, call.id)
            summary = output.summary
            records = [self._stamp(r, spec) for r in output.records]
            try:
                provenance_ids = await self.provenance.add_many(records, self.ctx.run_id, tool=spec.name)
            except Exception as exc:  # the call succeeded; don't lose it over a store hiccup
                await self.ctx.emit.emit(
                    "warning", {"code": "provenance_store_failed", "message": str(exc)}, call_id=call.id
                )
                provenance_ids = [r.id for r in records]
            full_result: Any = to_jsonable(output.data)
        else:
            assert error is not None
            content, truncated = f"{error.type}: {error.message}", False
            summary = error.message[:200]
            full_result = content

        self.ctx.scratchpad.add_tool_result(
            call.id,
            spec.name,
            call.args,
            full_result,
            status=status,
            provenance_ids=provenance_ids,
            duration_ms=duration_ms,
            parent_call_id=parent_call_id,
        )
        result = ToolResult(
            call_id=call.id,
            tool=spec.name,
            ok=status in ("ok", "partial"),
            status=status,
            summary=summary,
            content=content,
            truncated=truncated,
            error=error,
            provenance_ids=provenance_ids,
            duration_ms=duration_ms,
            data=output.data if output is not None else None,
        )
        return await self._finish(result, parent_call_id)

    def _stamp(self, record: ProvenanceRecord, spec: ToolSpec) -> ProvenanceRecord:
        update: dict[str, Any] = {}
        if record.run_id is None:
            update["run_id"] = self.ctx.run_id
        if record.tool is None:
            update["tool"] = spec.name
        return record.model_copy(update=update) if update else record
