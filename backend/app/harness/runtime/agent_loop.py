"""AgentLoop: iterative tool calling with context management and guards (D1-D5).

Ported from ``Agent.run`` in ``../dexter/src/agent/agent.ts``:

    while not stopped:
        guard.check → context_manager.before_llm_call → llm.complete(tools)
        record usage, context.usage; text → reasoning delta
        no tool calls → done (the text is the agent's brief)
        executor.run_calls → append assistant(tool_calls) + tool messages
        inject a tool-usage reminder when a tool is near its limit

Differences from Dexter: the answer is NOT the loop's last text (a separate
final-answer call writes it, P07 §7.4); a limit, cancel or LLM error ends
the loop gracefully with a ``stop_reason`` instead of an error answer.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

from pydantic import BaseModel

from app.domain.ports import LLMMessage, LLMPort, LLMResponse
from app.domain.tools.base import ToolSpec
from app.harness.context.manager import ContextManager
from app.harness.executor.executor import ToolExecutor
from app.harness.runtime.context import CancelledByUser, RunContext

logger = logging.getLogger(__name__)

NO_TOOLS_NUDGE = (
    "You have not called any tool yet, so there is no data to answer from. Call the tools you need NOW "
    "through the function-calling interface (not as text) — independent calls in this same turn. "
    "Do not answer from memory."
)


WRAP_UP_PROMPT = (
    "You have used all your tool-call rounds. Do NOT call any more tools. Write your final report NOW "
    "from the tool results above; state plainly what data was missing or unavailable."
)


class LoopOutcome(BaseModel):
    iterations: int
    stop_reason: str | None = None
    draft: str = ""
    tool_calls: int = 0


async def until_cancelled[T](ctx: RunContext, awaitable: Awaitable[T]) -> T:
    """Await ``awaitable`` unless the run's cancel token fires first (then abort it)."""
    task = asyncio.ensure_future(awaitable)
    waiter = asyncio.ensure_future(ctx.cancel.wait())
    try:
        done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        task.cancel()
        waiter.cancel()
        raise
    waiter.cancel()
    if task in done:
        return task.result()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    raise CancelledByUser(ctx.cancel.reason or "cancelled")


def usage_reminder(ctx: RunContext, tools: list[ToolSpec]) -> str | None:
    """``formatToolUsageForPrompt``: warn about tools at/near their per-run limit."""
    lines = []
    for spec in tools:
        if spec.limit_by:
            continue
        used = ctx.scratchpad.tool_call_count(ctx.scoped(spec.name))
        if used and used >= spec.max_calls_per_run - 1:
            left = max(0, spec.max_calls_per_run - used)
            lines.append(f"- {spec.name}: {used}/{spec.max_calls_per_run} used ({left} left)")
    if not lines:
        return None
    return (
        "<tool_usage_status>\n" + "\n".join(lines) + "\nPlan the remaining calls carefully; use results you "
        "already have.\n</tool_usage_status>"
    )


class AgentLoop:
    def __init__(
        self,
        ctx: RunContext,
        llm: LLMPort,
        executor: ToolExecutor,
        context: ContextManager,
    ) -> None:
        self.ctx = ctx
        self.llm = llm
        self.executor = executor
        self.context = context
        self.messages: list[LLMMessage] = []

    @property
    def started(self) -> bool:
        return bool(self.messages)

    def start(self, system: str, history: list[LLMMessage], user: str) -> None:
        self.messages = [
            LLMMessage(role="system", content=system),
            *history,
            LLMMessage(role="user", content=user),
        ]

    def set_system(self, system: str) -> None:
        if self.messages and self.messages[0].role == "system":
            self.messages[0] = LLMMessage(role="system", content=system)

    def add_user(self, text: str) -> None:
        self.messages.append(LLMMessage(role="user", content=text))

    async def _emit(self, type_: str, payload: dict[str, Any]) -> None:
        await self.ctx.emit.emit(type_, payload)

    async def _wrap_up(self) -> str:
        """One last call with no tools bound, so the model has to write instead of fetching more."""
        self.add_user(WRAP_UP_PROMPT)
        try:
            self.messages = await self.context.before_llm_call(self.ctx, self.messages)
            resp = await until_cancelled(
                self.ctx, self.llm.complete(self.messages, tier="deep", tools=None, purpose="agent")
            )
        except (CancelledByUser, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning("agent wrap-up call failed: %s", exc)
            await self._emit("warning", {"code": "llm_error", "message": f"{type(exc).__name__}: {exc}"})
            return ""
        text = resp.text.strip()
        if text:
            await self._emit("llm.delta", {"channel": "reasoning", "text": text})
            self.ctx.scratchpad.add_thinking(text)
        return text or (resp.reasoning or "").strip()

    async def run(
        self,
        tools: list[ToolSpec],
        *,
        max_iterations: int | None = None,
        require_data: bool = False,
        wrap_up: bool = False,
    ) -> LoopOutcome:
        ctx = self.ctx
        local = 0
        tool_calls = 0
        draft = ""
        stop: str | None = None
        nudged = False
        guard = ctx.guard
        while True:
            if ctx.cancel.cancelled:
                stop = "cancelled"
                break
            if max_iterations is not None:
                if local >= max_iterations:
                    if wrap_up and not draft and tool_calls:
                        draft = await self._wrap_up()
                    break
                hit = guard.check_runtime(ctx)
            else:
                hit = guard.check(ctx)
            if hit is not None:
                await self._emit("guard.limit", hit.model_dump())
                stop = f"{hit.kind}: {hit.detail}"
                break

            self.messages = await self.context.before_llm_call(ctx, self.messages)
            try:
                resp: LLMResponse = await until_cancelled(
                    ctx, self.llm.complete(self.messages, tier="deep", tools=tools, purpose="agent")
                )
            except CancelledByUser:
                stop = "cancelled"
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("agent LLM call failed: %s", exc)
                await self._emit("warning", {"code": "llm_error", "message": f"{type(exc).__name__}: {exc}"})
                stop = f"llm_error: {type(exc).__name__}"
                break
            ctx.iteration += 1
            local += 1
            await self.context.report_usage(ctx, self.messages)

            if resp.reasoning:
                await self._emit("llm.delta", {"channel": "reasoning", "text": resp.reasoning})
            text = resp.text.strip()
            if text:
                await self._emit("llm.delta", {"channel": "reasoning", "text": text})
                ctx.scratchpad.add_thinking(text)

            if not resp.tool_calls:
                if require_data and not nudged and tool_calls == 0 and tools:
                    nudged = True
                    self.messages.append(resp.as_message())
                    self.add_user(NO_TOOLS_NUDGE)
                    await self._emit(
                        "warning",
                        {"code": "no_tool_calls", "message": "model answered without tools; nudged once"},
                    )
                    continue
                # thinking models may leave the reply empty and put the brief in reasoning
                draft = text or (resp.reasoning or "").strip()
                break

            self.messages.append(resp.as_message())
            results = await self.executor.run_calls(resp.tool_calls)
            tool_calls += len(results)
            by_id = {r.call_id: r for r in results}
            for call in resp.tool_calls:
                result = by_id.get(call.id)
                content = result.content if result is not None else "Skipped: no result."
                self.messages.append(LLMMessage(role="tool", content=content, tool_call_id=call.id))
            reminder = usage_reminder(ctx, tools)
            if reminder:
                self.add_user(reminder)

        return LoopOutcome(iterations=local, stop_reason=stop, draft=draft, tool_calls=tool_calls)
