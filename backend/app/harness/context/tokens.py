"""Token accounting for context management (D1).

Ported from ``../dexter/src/utils/tokens.ts`` and
``../dexter/src/agent/token-counter.ts``: the real ``input_tokens`` of the
latest agent call is the context size; a chars/3.5 estimate is only a
fallback (before the first call, or right after the history was rewritten).
Threshold = context window - summary output reserve - autocompact buffer.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from typing import TYPE_CHECKING

from app.domain.ports import LLMMessage, LLMUsage
from app.harness.context.config import ContextConfig
from app.harness.llm.model_catalog import lookup

if TYPE_CHECKING:
    from app.harness.runtime.context import RunContext

# Only these calls see the agent's message list, so only they measure its size.
CONTEXT_PURPOSES = frozenset({"agent"})


def estimate_tokens(text: str, chars_per_token: float = 3.5) -> int:
    return math.ceil(len(text) / chars_per_token)


def message_text(message: LLMMessage) -> str:
    if not message.tool_calls:
        return message.content
    calls = json.dumps([c.model_dump() for c in message.tool_calls], ensure_ascii=False, default=str)
    return f"{message.content}\n{calls}"


def estimate_messages_tokens(messages: Iterable[LLMMessage], chars_per_token: float = 3.5) -> int:
    return estimate_tokens("\n".join(message_text(m) for m in messages), chars_per_token)


class TokenCounter:
    """Usage per purpose for the run + the latest real context size."""

    def __init__(self) -> None:
        self.last_api_input_tokens: int | None = None
        self.by_purpose: dict[str, LLMUsage] = {}

    def observe(self, usage: LLMUsage, purpose: str = "agent") -> None:
        self.by_purpose[purpose] = self.by_purpose.get(purpose, LLMUsage()) + usage
        if purpose in CONTEXT_PURPOSES and usage.input_tokens:
            self.last_api_input_tokens = usage.input_tokens

    def discount(self, tokens: int) -> None:
        """Messages shrank by ~``tokens`` since the last API call (microcompact)."""
        if self.last_api_input_tokens is not None:
            self.last_api_input_tokens = max(0, self.last_api_input_tokens - tokens)

    def invalidate(self) -> None:
        """History was rewritten: estimate until the next agent call reports real usage."""
        self.last_api_input_tokens = None

    def total(self) -> LLMUsage:
        out = LLMUsage()
        for usage in self.by_purpose.values():
            out = out + usage
        return out


def current_context_tokens(
    ctx: RunContext, messages: list[LLMMessage], config: ContextConfig | None = None
) -> int:
    last = ctx.token_counter.last_api_input_tokens
    if last is not None and last > 0:
        return last
    cpt = (config or ContextConfig()).chars_per_token
    return estimate_messages_tokens(messages, cpt)


def context_window(model: str, config: ContextConfig | None = None) -> int:
    config = config or ContextConfig()
    info = lookup(model)
    return info.context_window if info.known else config.default_context_window


SMALL_WINDOW_RESERVE_RATIO = 0.35


def compact_threshold(model: str, config: ContextConfig | None = None) -> int:
    """``getAutoCompactThreshold``: window - summary reserve - buffer (200k → 167k, unknown → 95k).

    The fixed 33k reserve would exceed a small window (ThaiLLM 16k), so the
    reserve is capped at 35% of the window there (16k → ~10.6k).
    """
    config = config or ContextConfig()
    window = context_window(model, config)
    reserve = config.max_output_tokens_for_summary + config.autocompact_buffer_tokens
    return window - min(reserve, int(window * SMALL_WINDOW_RESERVE_RATIO))


def usage_percent(used: int, window: int) -> float:
    if window <= 0:
        return 0.0
    return round(min(100.0, used / window * 100), 1)
