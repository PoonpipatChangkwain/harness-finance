"""Test doubles for the LLM layer (no network)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage

from app.harness.llm.base import BaseLLMClient
from app.harness.llm.config import PROVIDERS, LLMTierConfig
from app.harness.llm.model_catalog import ModelInfo


class StatusError(Exception):
    """Mimics an SDK error carrying an HTTP status code."""

    def __init__(self, status_code: int, message: str = "") -> None:
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


def fake_client(
    responses: list[AIMessage], *, provider: str = "anthropic", model: str = "claude-sonnet-5"
) -> BaseLLMClient:
    cfg = LLMTierConfig(provider=provider, model=model)
    chat = FakeMessagesListChatModel(responses=responses)
    return BaseLLMClient(cfg, PROVIDERS[provider], chat, ModelInfo(input_per_m=2.0, output_per_m=10.0))


class ScriptedClient(BaseLLMClient):
    """A client whose native-structured and free-text answers are scripted.

    ``native`` is what the structured runnable returns (a dict like
    ``{"raw": ..., "parsed": ..., "parsing_error": ...}``) or an exception;
    ``None`` means the provider has no structured support.
    """

    def __init__(self, native: Any, freetext: AIMessage | BaseException, *, label: str = "anthropic") -> None:
        super().__init__(
            LLMTierConfig(provider=label, model="m"),
            PROVIDERS[label],
            FakeMessagesListChatModel(responses=[]),
            ModelInfo(),
        )
        self.native = native
        self.freetext = freetext
        self.freetext_calls: list[list[BaseMessage]] = []

    def with_structured(self, schema: Any) -> Any:
        return None if self.native is None else object()

    async def ainvoke_runnable(self, runnable: Any, messages: Sequence[BaseMessage]) -> Any:
        if isinstance(self.native, BaseException):
            raise self.native
        return self.native

    async def ainvoke(self, messages: Sequence[BaseMessage], *, tools: Any = None) -> AIMessage:
        self.freetext_calls.append(list(messages))
        if isinstance(self.freetext, BaseException):
            raise self.freetext
        return self.freetext
