"""Round-aware message surgery: fallback truncation + tool-call pairing check (D1).

``truncate_messages`` ports ``truncateMessages`` from
``../dexter/src/agent/agent.ts``: drop whole rounds (assistant ``tool_calls``
+ their tool results + any reminder that followed) oldest first, keeping
everything before the first round (system, history, the query). A round is
never split, so providers never see a tool result without its call.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.domain.ports import LLMMessage


class ToolPairingError(ValueError):
    pass


class TruncateResult(BaseModel):
    messages: list[LLMMessage]
    removed_messages: int = 0
    removed_call_ids: list[str] = []
    kept_tool_results: int = 0


def _round_starts(messages: list[LLMMessage]) -> list[int]:
    return [i for i, m in enumerate(messages) if m.role == "assistant" and m.tool_calls]


def truncate_messages(messages: list[LLMMessage], keep_tool_uses: int = 5) -> TruncateResult:
    """Keep the newest rounds holding at least ``keep_tool_uses`` tool results (≥ 1 round)."""
    starts = _round_starts(messages)
    if not starts:
        return TruncateResult(messages=list(messages))
    bounds = [(s, starts[k + 1] if k + 1 < len(starts) else len(messages)) for k, s in enumerate(starts)]

    kept_from = len(bounds)
    kept_tools = 0
    while kept_from > 0 and (kept_tools < keep_tool_uses or kept_from == len(bounds)):
        kept_from -= 1
        start, end = bounds[kept_from]
        kept_tools += sum(1 for m in messages[start:end] if m.role == "tool")
    if kept_from == 0:
        return TruncateResult(messages=list(messages), kept_tool_results=kept_tools)

    cut_start, cut_end = bounds[0][0], bounds[kept_from][0]
    removed = messages[cut_start:cut_end]
    call_ids = [m.tool_call_id for m in removed if m.role == "tool" and m.tool_call_id]
    return TruncateResult(
        messages=[*messages[:cut_start], *messages[cut_end:]],
        removed_messages=len(removed),
        removed_call_ids=call_ids,
        kept_tool_results=kept_tools,
    )


def assert_tool_pairing(messages: list[LLMMessage]) -> None:
    """Every tool result answers a call of the latest assistant turn, and every call is answered."""
    open_calls: set[str] = set()
    for i, message in enumerate(messages):
        if message.role == "assistant":
            if open_calls:
                raise ToolPairingError(f"message {i}: calls {sorted(open_calls)} have no tool result")
            open_calls = {c.id for c in message.tool_calls}
        elif message.role == "tool":
            if message.tool_call_id not in open_calls:
                raise ToolPairingError(f"message {i}: tool result {message.tool_call_id!r} has no call")
            open_calls.discard(message.tool_call_id)
        elif message.role == "user" and open_calls:
            raise ToolPairingError(f"message {i}: user turn before results for {sorted(open_calls)}")
    if open_calls:
        raise ToolPairingError(f"calls {sorted(open_calls)} have no tool result")
