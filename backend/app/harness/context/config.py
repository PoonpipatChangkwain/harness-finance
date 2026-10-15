"""Context-management constants (D1), in one place and overridable from settings.

Ported from ``../dexter/src/agent/microcompact.ts`` (count/token triggers,
keep-recent), ``../dexter/src/agent/compact.ts`` (failure cap, minimum tool
results) and ``../dexter/src/utils/tokens.ts`` (summary reserve, autocompact
buffer, default window, keep-tool-uses, 3.5 chars per token).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.settings import Settings

MC_CLEARED_PREFIX = "[Old tool result content cleared"
MC_CLEARED_MESSAGE = "[Old tool result content cleared — full result in scratchpad call_id={id}]"


class ContextConfig(BaseModel):
    mc_count_trigger: int | None = 8  # None = count trigger off (token trigger only)
    mc_keep_recent: int = 4
    mc_token_trigger: int = 80_000
    # Read-only tools in these categories may have their results cleared.
    compactable_categories: frozenset[str] = frozenset({"data", "search", "memory"})
    max_output_tokens_for_summary: int = 20_000
    autocompact_buffer_tokens: int = 13_000
    default_context_window: int = 128_000
    keep_tool_uses: int = 5
    max_consecutive_compaction_failures: int = 3
    min_tool_results_for_compaction: int = 3
    chars_per_token: float = 3.5
    memory_flush: bool = True
    memory_namespace: str = "research_notes"
    check_pairing: bool = False

    @classmethod
    def from_settings(
        cls, settings: Settings, *, provider: str | None = None, **overrides: Any
    ) -> ContextConfig:
        """``provider`` is the agent's (deep tier) LLM provider; claude_code gets its own
        count trigger (``CLAUDE_CODE_MC_COUNT_TRIGGER``, default off)."""
        mapping = {
            "mc_count_trigger": settings.context_mc_count_trigger,
            "mc_keep_recent": settings.context_mc_keep_recent,
            "mc_token_trigger": settings.context_mc_token_trigger,
            "max_output_tokens_for_summary": settings.context_max_output_tokens_for_summary,
            "autocompact_buffer_tokens": settings.context_autocompact_buffer_tokens,
            "keep_tool_uses": settings.context_keep_tool_uses,
            "max_consecutive_compaction_failures": settings.context_max_compaction_failures,
            "min_tool_results_for_compaction": settings.context_min_tool_results,
            "memory_flush": settings.context_memory_flush,
            "check_pairing": settings.context_check_pairing,
        }
        values = {k: v for k, v in {**mapping, **overrides}.items() if v is not None}
        if provider == "claude_code" and "mc_count_trigger" not in overrides:
            values["mc_count_trigger"] = settings.claude_code_mc_count_trigger
        return cls(**values)
