"""Conversation state: the messages sent to the LLM (D2).

Separate from the scratchpad on purpose: this list may be compacted (P06),
the scratchpad never is. Final answers and provenance come from the
scratchpad, not from here.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.ports import LLMMessage


class Conversation:
    def __init__(self, messages: Iterable[LLMMessage] | None = None) -> None:
        self._messages: list[LLMMessage] = list(messages or [])

    def __len__(self) -> int:
        return len(self._messages)

    @property
    def messages(self) -> list[LLMMessage]:
        return list(self._messages)

    def add(self, message: LLMMessage) -> None:
        self._messages.append(message)

    def extend(self, messages: Iterable[LLMMessage]) -> None:
        self._messages.extend(messages)

    def system(self, content: str) -> None:
        """Set/replace the leading system message."""
        if self._messages and self._messages[0].role == "system":
            self._messages[0] = LLMMessage(role="system", content=content)
        else:
            self._messages.insert(0, LLMMessage(role="system", content=content))

    def replace(self, messages: Iterable[LLMMessage]) -> None:
        """Swap in a compacted history."""
        self._messages = list(messages)

    def tool_messages(self) -> list[tuple[int, LLMMessage]]:
        return [(i, m) for i, m in enumerate(self._messages) if m.role == "tool"]

    def set_content(self, index: int, content: str) -> None:
        self._messages[index] = self._messages[index].model_copy(update={"content": content})

    def char_count(self) -> int:
        return sum(len(m.content) for m in self._messages)
