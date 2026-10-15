"""Microcompact: per-turn clearing of old tool-result content, no LLM (D1).

Ported from ``../dexter/src/agent/microcompact.ts``. Differences:
- "compactable" is decided by the tool's spec (read-only and category in
  ``compactable_categories``) instead of a hard-coded name list
- the cleared marker names the ``call_id`` so the full result can be found
  in the scratchpad; ``tool_call_id`` is kept so provider pairing stays valid
- always returns a new list (never mutates the input)
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from app.domain.ports import LLMMessage
from app.domain.tools.base import ToolSpec
from app.harness.context.config import MC_CLEARED_MESSAGE, MC_CLEARED_PREFIX, ContextConfig
from app.harness.context.tokens import estimate_tokens
from app.harness.state.scratchpad import Scratchpad


class ToolLookup(Protocol):
    def get(self, name: str) -> ToolSpec | None: ...


class MicrocompactResult(BaseModel):
    messages: list[LLMMessage]
    cleared: int = 0
    cleared_call_ids: list[str] = []
    tokens_saved: int = 0
    trigger: Literal["count", "token"] | None = None


def is_cleared_content(content: str) -> bool:
    return content.startswith(MC_CLEARED_PREFIX)


def tool_names_by_call_id(messages: list[LLMMessage]) -> dict[str, str]:
    """Tool messages carry only ``tool_call_id``; the name lives on the assistant turn."""
    names: dict[str, str] = {}
    for message in messages:
        for call in message.tool_calls:
            names[call.id] = call.name
    return names


def is_compactable(spec: ToolSpec | None, config: ContextConfig) -> bool:
    return spec is not None and spec.read_only and spec.category in config.compactable_categories


def microcompact(
    messages: list[LLMMessage],
    *,
    tools: ToolLookup,
    scratchpad: Scratchpad | None = None,
    config: ContextConfig | None = None,
) -> MicrocompactResult:
    config = config or ContextConfig()
    names = tool_names_by_call_id(messages)
    candidates: list[int] = []
    for i, message in enumerate(messages):
        if message.role != "tool" or message.tool_call_id is None or is_cleared_content(message.content):
            continue
        if is_compactable(tools.get(names.get(message.tool_call_id, "")), config):
            candidates.append(i)

    trigger: Literal["count", "token"] | None = None
    if config.mc_count_trigger is not None and len(candidates) > config.mc_count_trigger:
        trigger = "count"
    else:
        total = sum(estimate_tokens(messages[i].content, config.chars_per_token) for i in candidates)
        if total > config.mc_token_trigger:
            trigger = "token"
    if trigger is None:
        return MicrocompactResult(messages=list(messages))

    keep = set(candidates[-config.mc_keep_recent :]) if config.mc_keep_recent > 0 else set()
    clear = [i for i in candidates if i not in keep]
    if not clear:
        return MicrocompactResult(messages=list(messages))

    out = list(messages)
    saved = 0
    call_ids: list[str] = []
    for i in clear:
        message = messages[i]
        call_id = message.tool_call_id or ""
        saved += estimate_tokens(message.content, config.chars_per_token)
        call_ids.append(call_id)
        out[i] = message.model_copy(update={"content": MC_CLEARED_MESSAGE.format(id=call_id)})

    if scratchpad is not None:
        scratchpad.mark_cleared(call_ids)
    return MicrocompactResult(
        messages=out, cleared=len(clear), cleared_call_ids=call_ids, tokens_saved=saved, trigger=trigger
    )
