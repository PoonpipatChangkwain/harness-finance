"""What the LLM and the UI see after a tool call is normalized (contracts.md §6)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ToolResultStatus = Literal[
    "ok", "partial", "unavailable", "error", "denied", "limited", "timeout", "cancelled"
]

PREVIEW_CHARS = 2_000


class ToolError(BaseModel):
    type: str
    message: str
    retryable: bool = False


class ToolResult(BaseModel):
    call_id: str
    tool: str
    ok: bool
    status: ToolResultStatus
    summary: str
    content: str
    truncated: bool = False
    error: ToolError | None = None
    provenance_ids: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    # (P10) the tool's full, untruncated ``ToolOutput.data`` for workflow nodes that compute
    # from it (e.g. the trader sizing off the snapshot). Never serialized: not in events/LLM.
    data: Any = Field(default=None, exclude=True)

    def finished_payload(self) -> dict[str, Any]:
        """``tool.finished`` payload: everything but the full content, plus a ≤2k preview."""
        data = self.model_dump(exclude={"content"})
        data["preview"] = self.content[:PREVIEW_CHARS]
        return data
