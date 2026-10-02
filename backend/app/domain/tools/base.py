"""Framework-neutral tool contract (D3/D4, contracts.md §6).

A tool is a plain async function plus a Pydantic args model. The harness
executor turns a ``ToolSpec`` into whatever the LLM framework needs
(JSON schema from ``args_model.model_json_schema()``), validates args,
enforces permissions/limits/timeouts and normalizes the ``ToolOutput``.
Ported pattern from ``../dexter/src/tools/types.ts`` and
``../FinRobot/finrobot/toolkits.py`` (register a plain function with any agent).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.data.base import Capability
from app.domain.models.provenance import ProvenanceRecord
from app.domain.ports import ToolCallReq

ToolCategory = Literal["data", "compute", "search", "memory", "skill", "portfolio", "meta"]
ToolStatus = Literal["ok", "partial", "unavailable"]


class ToolContext(Protocol):
    """What a tool may ask of the harness while it runs."""

    run_id: str
    call_id: str

    async def progress(self, message: str) -> None: ...

    def is_cancelled(self) -> bool: ...

    async def call_tools(self, calls: list[ToolCallReq]) -> list[Any]:
        """Run nested tool calls through the executor (skills); results carry ``parent_call_id``."""
        ...


class ToolOutput(BaseModel):
    data: Any
    summary: str
    records: list[ProvenanceRecord] = Field(default_factory=list)
    status: ToolStatus = "ok"


ToolFn = Callable[[Any, ToolContext], Awaitable[ToolOutput]]


class ToolSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    args_model: type[BaseModel]
    read_only: bool = True
    category: ToolCategory
    timeout_s: float = 30
    max_calls_per_run: int = 3
    requires_approval: bool = False
    # Data capabilities this tool depends on; the registry only exposes the tool
    # when at least one provider for each capability is configured.
    requires: frozenset[Capability] = frozenset()
    # (P07) Name of an arg whose value partitions ``max_calls_per_run``: the skill
    # tool uses "skill" so each skill runs once per query, independently; per-symbol
    # data/compute tools use "symbol" so an 11-holding portfolio isn't capped at 3.
    limit_by: str | None = None
    # (P10) Called by workflow nodes through the executor (validation, provenance,
    # scratchpad) but never offered to an LLM.
    internal: bool = False
    fn: ToolFn

    def limit_key(self, args: dict[str, Any]) -> str:
        if self.limit_by is None:
            return self.name
        value = args.get(self.limit_by)
        if isinstance(value, str):
            value = value.strip().upper()  # "aapl" and "AAPL " share one budget
        return f"{self.name}:{value}"

    def json_schema(self) -> dict[str, Any]:
        return self.args_model.model_json_schema()
