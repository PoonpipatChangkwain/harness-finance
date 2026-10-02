"""RunContext + CancelToken + RunUsage.

Ported from ``../dexter/src/agent/run-context.ts`` (run id, query,
scratchpad, token counter, start time, iteration) with the D5 additions:
usage accounting, a cancel token checked at every await point, and the
run's limits.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.ports import EventPort, LLMUsage
from app.harness.context.tokens import TokenCounter
from app.harness.runtime.limits import LimitGuard, RunLimits
from app.harness.state.scratchpad import Scratchpad


class CancelledByUser(Exception):
    pass


class CancelToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.reason: str | None = None

    def cancel(self, reason: str = "cancelled") -> None:
        self.reason = reason
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledByUser(self.reason or "cancelled")

    async def wait(self) -> None:
        await self._event.wait()


class RunUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    # purpose → {input_tokens, output_tokens, cost_usd, calls} (StatsPills breakdown)
    by_purpose: dict[str, dict[str, float]] = Field(default_factory=dict)

    def add_llm(self, usage: LLMUsage, purpose: str = "agent") -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_read_tokens += usage.cache_read_tokens
        self.cost_usd = round(self.cost_usd + usage.cost_usd, 8)
        self.llm_calls += 1
        slot = self.by_purpose.setdefault(
            purpose, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}
        )
        slot["input_tokens"] += usage.input_tokens
        slot["output_tokens"] += usage.output_tokens
        slot["cost_usd"] = round(slot["cost_usd"] + usage.cost_usd, 8)
        slot["calls"] += 1


@dataclass
class RunContext:
    run_id: str
    workflow: str
    input: dict[str, Any]
    emit: EventPort
    scratchpad: Scratchpad
    limits: RunLimits
    session_id: str | None = None
    query: str = ""
    cancel: CancelToken = field(default_factory=CancelToken)
    usage: RunUsage = field(default_factory=RunUsage)
    token_counter: TokenCounter = field(default_factory=TokenCounter)
    iteration: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_monotonic: float = field(default_factory=time.monotonic)
    services: Any = None  # HarnessServices, set by RunManager
    # A workflow that ended gracefully on a limit sets these (run.finished = "limited").
    outcome_status: str | None = None
    outcome_reason: str | None = None
    # Concurrent analysts share one scratchpad; a scope keeps each one's per-tool call budget and
    # duplicate check separate, so one analyst's call never blocks another's.
    guard_scope: str = ""

    def scoped(self, key: str) -> str:
        return f"{self.guard_scope}|{key}" if self.guard_scope else key

    @property
    def guard(self) -> LimitGuard:
        return LimitGuard(self.limits)

    @property
    def last_api_input_tokens(self) -> int | None:
        return self.token_counter.last_api_input_tokens

    def record_llm_usage(self, usage: LLMUsage, purpose: str = "agent") -> None:
        self.usage.add_llm(usage, purpose)
        self.token_counter.observe(usage, purpose)
