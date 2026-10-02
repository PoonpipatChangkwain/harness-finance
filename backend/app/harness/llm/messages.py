"""LLMMessage ↔ LangChain message conversion, ToolSpec → tool schema.

The only place where the framework-neutral message shape from
``app.domain.ports`` meets LangChain types.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from ulid import ULID

from app.domain.ports import LLMMessage, ToolCallReq
from app.domain.tools.base import ToolSpec


def new_call_id() -> str:
    return f"call_{ULID()}"


def to_langchain(messages: list[LLMMessage]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in messages:
        if m.role == "system":
            out.append(SystemMessage(content=m.content))
        elif m.role == "user":
            out.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            # Replay the provider's own content blocks (e.g. Claude thinking blocks
            # that must precede tool_use) when we have them.
            content: Any = m.provider_content if m.provider_content else m.content
            out.append(
                AIMessage(
                    content=content,
                    tool_calls=[
                        {"id": c.id, "name": c.name, "args": c.args, "type": "tool_call"}
                        for c in m.tool_calls
                    ],
                )
            )
        else:
            out.append(ToolMessage(content=m.content, tool_call_id=m.tool_call_id or ""))
    return out


Channel = Literal["answer", "reasoning"]

_THINK_RE = re.compile(r"<think>(.*?)(?:</think>|$)", re.DOTALL)


def _split_think(text: str) -> tuple[str, str]:
    """Inline ``<think>…</think>`` (ThaiLLM / Qwen via vLLM) → reasoning; unclosed = truncated."""
    if "<think>" not in text:
        return text, ""
    reasoning = "".join(m.group(1) for m in _THINK_RE.finditer(text))
    return _THINK_RE.sub("", text).strip(), reasoning.strip()


class ThinkStreamSplitter:
    """Streaming counterpart of ``_split_think``: routes text between ``<think>`` tags to reasoning.

    Tags may arrive split across chunks, so a trailing partial tag is held back
    until the next chunk (or ``flush``) decides it.
    """

    def __init__(self) -> None:
        self._in_think = False
        self._pending = ""

    def feed(self, text: str) -> list[tuple[Channel, str]]:
        buf = self._pending + text
        self._pending = ""
        out: list[tuple[Channel, str]] = []
        while buf:
            tag = "</think>" if self._in_think else "<think>"
            channel: Channel = "reasoning" if self._in_think else "answer"
            idx = buf.find(tag)
            if idx != -1:
                if idx:
                    out.append((channel, buf[:idx]))
                buf = buf[idx + len(tag) :]
                self._in_think = not self._in_think
                continue
            keep = next((n for n in range(len(tag) - 1, 0, -1) if buf.endswith(tag[:n])), 0)
            if len(buf) > keep:
                out.append((channel, buf[: len(buf) - keep]))
            self._pending = buf[len(buf) - keep :]
            break
        return out

    def flush(self) -> list[tuple[Channel, str]]:
        pending, self._pending = self._pending, ""
        return [("reasoning" if self._in_think else "answer", pending)] if pending else []


def split_content(content: Any, *, inline_think: bool = True) -> tuple[str, str]:
    """(answer text, reasoning text) from a str or a list of provider content blocks.

    ``inline_think=False`` leaves ``<think>`` tags in the answer for a
    ``ThinkStreamSplitter`` to handle across chunks.
    """
    if isinstance(content, str):
        return _split_think(content) if inline_think else (content, "")
    answer: list[str] = []
    reasoning: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            answer.append(block)
        elif isinstance(block, dict):
            kind = block.get("type")
            if kind == "text":
                answer.append(str(block.get("text", "")))
            elif kind in ("thinking", "reasoning"):
                reasoning.append(
                    str(block.get("thinking") or block.get("reasoning") or block.get("text") or "")
                )
    if not inline_think:
        return "".join(answer), "".join(reasoning)
    text, inline = _split_think("".join(answer))
    return text, "".join(reasoning) + inline


def tool_calls_of(message: AIMessage) -> list[ToolCallReq]:
    return [
        ToolCallReq(id=tc.get("id") or new_call_id(), name=tc["name"], args=dict(tc.get("args") or {}))
        for tc in message.tool_calls
    ]


def tool_schema(spec: ToolSpec) -> dict[str, Any]:
    """OpenAI-format function tool; LangChain converts it for every provider."""
    params = spec.json_schema()
    params.pop("title", None)
    return {
        "type": "function",
        "function": {"name": spec.name, "description": spec.description, "parameters": params},
    }
