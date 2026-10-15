"""BaseLLMClient: one provider+model, wrapping a LangChain chat model.

Ported from ``../TradingAgents/tradingagents/llm_clients/base_client.py``
plus the Anthropic prompt-caching trick from ``../dexter/src/model/llm.ts``
(``annotateSystemMessageForCaching``). Retries and failover live one level
up (``failover.py``); this class is a thin, timeout-bounded call site so
the SDK's own retries are disabled (``max_retries=0``) and our policy is
the only one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from app.harness.llm.config import LLMTierConfig, ProviderInfo
from app.harness.llm.model_catalog import ModelInfo

logger = logging.getLogger(__name__)

_structured_warned: set[tuple[str, str, str]] = set()


class BaseLLMClient:
    def __init__(
        self, cfg: LLMTierConfig, provider: ProviderInfo, chat_model: BaseChatModel, info: ModelInfo
    ):
        self.cfg = cfg
        self.provider_info = provider
        self.chat_model = chat_model
        self.info = info

    @property
    def provider(self) -> str:
        return self.provider_info.id

    @property
    def model(self) -> str:
        return self.cfg.model

    def prepare(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        """Anthropic: mark the leading system prompt ``cache_control: ephemeral`` (~90% cheaper re-reads).

        Not for ``claude_code``: the CLI manages prompt caching itself and takes plain text.
        """
        msgs = list(messages)
        if self.provider_info.kind != "anthropic" or not msgs or not isinstance(msgs[0], SystemMessage):
            return msgs
        system = msgs[0]
        if isinstance(system.content, str):
            text = system.content
            msgs[0] = SystemMessage(
                content=[{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
            )
        return msgs

    def bind_tools(self, tools: list[dict[str, Any]]) -> Runnable[Any, Any]:
        return self.chat_model.bind_tools(tools)

    def with_structured(self, schema: type[BaseModel]) -> Runnable[Any, Any] | None:
        """``with_structured_output(schema, include_raw=True)`` or None if the provider can't.

        Anthropic uses native JSON-schema output: forced tool calling (LangChain's
        default) is rejected by Claude Opus 5.5 / Fable 5.1.
        """
        method = self.provider_info.structured_method
        try:
            return self.chat_model.with_structured_output(schema, method=method, include_raw=True)
        except (NotImplementedError, AttributeError, ValueError, TypeError) as exc:
            key = (self.provider, self.model, schema.__name__)
            if key not in _structured_warned:
                _structured_warned.add(key)
                logger.warning(
                    "%s:%s does not support structured output for %s (%s); using free-text fallback",
                    self.provider,
                    self.model,
                    schema.__name__,
                    exc,
                )
            return None

    async def ainvoke(
        self, messages: Sequence[BaseMessage], *, tools: list[dict[str, Any]] | None = None
    ) -> AIMessage:
        runnable: Runnable[Any, Any] = self.bind_tools(tools) if tools else self.chat_model
        result = await asyncio.wait_for(runnable.ainvoke(self.prepare(messages)), timeout=self.cfg.timeout_s)
        if not isinstance(result, AIMessage):
            raise TypeError(f"expected AIMessage from {self.provider}, got {type(result).__name__}")
        return result

    async def ainvoke_runnable(self, runnable: Runnable[Any, Any], messages: Sequence[BaseMessage]) -> Any:
        return await asyncio.wait_for(runnable.ainvoke(self.prepare(messages)), timeout=self.cfg.timeout_s)

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        async for chunk in self.chat_model.astream(self.prepare(messages)):
            if isinstance(chunk, AIMessageChunk):
                yield chunk
            elif isinstance(chunk, AIMessage):
                # Models without native streaming yield one whole message.
                yield AIMessageChunk(
                    content=chunk.content,
                    usage_metadata=chunk.usage_metadata,
                    response_metadata=chunk.response_metadata,
                )
