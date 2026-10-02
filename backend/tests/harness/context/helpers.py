"""Message/scratchpad builders shared by the context tests."""

from __future__ import annotations

from app.domain.ports import LLMMessage, ToolCallReq
from app.harness.runtime.context import RunContext


def system(text: str = "SYSTEM PROMPT") -> LLMMessage:
    return LLMMessage(role="system", content=text)


def user(text: str) -> LLMMessage:
    return LLMMessage(role="user", content=text)


def round_(i: int, tool: str = "get_prices", content: str = "result", n_calls: int = 1) -> list[LLMMessage]:
    """One agent round: an assistant turn with ``n_calls`` tool calls + their results."""
    calls = [ToolCallReq(id=f"call_{i}_{k}", name=tool, args={"symbol": f"S{i}{k}"}) for k in range(n_calls)]
    out = [LLMMessage(role="assistant", content="", tool_calls=calls)]
    out += [LLMMessage(role="tool", content=content, tool_call_id=c.id) for c in calls]
    return out


def conversation(rounds: int, **kw: object) -> list[LLMMessage]:
    msgs = [system(), user("วิเคราะห์ PTT")]
    for i in range(rounds):
        msgs += round_(i, **kw)  # type: ignore[arg-type]
    return msgs


def fill_scratchpad(ctx: RunContext, messages: list[LLMMessage], provenance: bool = True) -> None:
    """Mirror each tool message into the scratchpad the way the executor does (full result)."""
    names = {c.id: c.name for m in messages for c in m.tool_calls}
    for m in messages:
        if m.role == "tool" and m.tool_call_id:
            prov = [f"prov_{m.tool_call_id.replace('_', '').upper()}"] if provenance else []
            ctx.scratchpad.add_tool_result(
                m.tool_call_id,
                names[m.tool_call_id],
                {"symbol": "PTT"},
                {"close": 34.5, "full": m.content},
                provenance_ids=prov,
            )
