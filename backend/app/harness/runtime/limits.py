"""Run guards (D5): iterations, total tool calls, wall clock, cost and output-token budget.

Ported from ``maxIterations`` in ``../dexter/src/agent/agent.ts`` and the
timeout policy in ``../deepseek-harness/packages/guard/timeout-policy``.
``LimitGuard.check`` runs before every LLM call and every tool batch; a hit
is reported as ``guard.limit`` and the caller stops gracefully (P07 builds
the answer from the scratchpad instead of crashing).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from app.settings import Settings

if TYPE_CHECKING:
    from app.harness.runtime.context import RunContext

LimitKind = Literal["iterations", "tool_calls", "similar_query", "timeout", "budget"]


class RunLimits(BaseModel):
    max_iterations: int = 10
    max_tool_calls_total: int = 40
    max_calls_per_tool: int = 3
    similarity_threshold: float = 0.7
    tool_timeout_s: float = 30
    llm_timeout_s: float = 120
    run_wall_clock_s: float = 600
    max_cost_usd: float = 1.0
    max_output_tokens_total: int = 200_000

    @classmethod
    def from_settings(cls, settings: Settings, **overrides: object) -> RunLimits:
        base = cls(
            max_iterations=settings.run_max_iterations,
            max_tool_calls_total=settings.run_max_tool_calls_total,
            tool_timeout_s=settings.run_tool_timeout_s,
            llm_timeout_s=settings.llm_timeout_s,
            run_wall_clock_s=settings.run_wall_clock_s,
            max_cost_usd=settings.run_max_cost_usd,
            max_output_tokens_total=settings.run_max_output_tokens_total,
            max_calls_per_tool=settings.run_max_calls_per_tool,
            similarity_threshold=settings.run_similarity_threshold,
        )
        return base.model_copy(update={k: v for k, v in overrides.items() if v is not None})


class LimitHit(BaseModel):
    kind: LimitKind
    detail: str


class LimitExceeded(Exception):
    """Raised by workflows that choose to stop on a limit; the run ends as ``limited``."""

    def __init__(self, hit: LimitHit) -> None:
        super().__init__(f"{hit.kind}: {hit.detail}")
        self.hit = hit


class LimitGuard:
    def __init__(self, limits: RunLimits) -> None:
        self.limits = limits

    def check(self, ctx: RunContext, *, pending_tool_calls: int = 0) -> LimitHit | None:
        lim = self.limits
        if ctx.iteration >= lim.max_iterations:
            return LimitHit(kind="iterations", detail=f"reached {lim.max_iterations} iterations")
        if ctx.usage.tool_calls + pending_tool_calls > lim.max_tool_calls_total:
            return LimitHit(
                kind="tool_calls",
                detail=f"{ctx.usage.tool_calls} tool calls used; limit {lim.max_tool_calls_total} per run",
            )
        return self.check_runtime(ctx)

    def check_runtime(self, ctx: RunContext) -> LimitHit | None:
        """Wall clock + cost/token budget only (what gates a tool batch)."""
        lim = self.limits
        elapsed = time.monotonic() - ctx.started_monotonic
        if elapsed >= lim.run_wall_clock_s:
            return LimitHit(kind="timeout", detail=f"run exceeded {lim.run_wall_clock_s:.0f}s wall clock")
        if ctx.usage.cost_usd >= lim.max_cost_usd:
            return LimitHit(
                kind="budget", detail=f"cost ${ctx.usage.cost_usd:.4f} reached ${lim.max_cost_usd:.2f} cap"
            )
        if ctx.usage.output_tokens >= lim.max_output_tokens_total:
            return LimitHit(
                kind="budget",
                detail=f"{ctx.usage.output_tokens} output tokens reached {lim.max_output_tokens_total} cap",
            )
        return None

    async def enforce(self, ctx: RunContext, *, pending_tool_calls: int = 0) -> LimitHit | None:
        """``check`` + emit ``guard.limit`` when hit."""
        hit = self.check(ctx, pending_tool_calls=pending_tool_calls)
        if hit is not None:
            await ctx.emit.emit("guard.limit", hit.model_dump())
        return hit
