"""(P17) Ports a workflow node uses for memory (contracts.md §13.4).

The harness puts a ``MemoryPort`` (``app.harness.memory.engine.MemoryEngine``) in
``ctx.config["memory"]``; its ``bank`` is the shared ReasoningBank. Pass the node's own
``ctx.llm`` / ``ctx.emit`` so the memory LLM calls show up (and are billed) in the run, with
``purpose="memory"``. Memory is only *written* from a ``learn`` node (D2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Protocol

from app.domain.memory.schemas import (
    DiscoveryAssessment,
    MemoryChange,
    OutcomeJudgement,
    OutcomeSignal,
    ReasoningItem,
    Recall,
)
from app.domain.ports import EventPort, LLMPort


class ReasoningBankPort(Protocol):
    async def retrieve(
        self, query: str, tags: Mapping[str, Any] | None = None, *, k: int = 2
    ) -> list[ReasoningItem]: ...

    async def record_use(self, item_ids: Sequence[str], run_id: str, user_id: str) -> None: ...

    async def settle(self, run_id: str, success: bool) -> list[ReasoningItem]: ...

    async def distill(
        self,
        *,
        run_summary: str,
        outcome: OutcomeJudgement,
        tags: Mapping[str, Any],
        source_run_id: str | None,
        user_id: str,
        outcome_signal: OutcomeSignal | None = None,
        max_items: int = 3,
        llm: LLMPort | None = None,
        emit: EventPort | None = None,
        node: str | None = None,
    ) -> list[ReasoningItem]: ...


class MemoryPort(Protocol):
    @property
    def bank(self) -> ReasoningBankPort: ...

    async def recall(
        self, user_id: str, query: str, *, workflow: str, tags: Mapping[str, Any] | None = None
    ) -> Recall: ...

    async def learn(
        self,
        user_id: str,
        *,
        user_messages: Sequence[str],
        assistant_text: str = "",
        discovery: DiscoveryAssessment | None = None,
        source_ref: Mapping[str, Any] | None = None,
        observation_date: date | None = None,
        llm: LLMPort | None = None,
        emit: EventPort | None = None,
        node: str | None = None,
    ) -> list[MemoryChange]: ...
