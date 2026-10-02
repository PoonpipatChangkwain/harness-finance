"""HarnessLLM: the ``LLMPort`` implementation domain code talks to.

Converts ``LLMMessage`` ↔ LangChain, ``ToolSpec`` → tool schema, routes a
call to the deep or quick ``FailoverLLM`` chain, reports real usage/cost,
and (when bound to a run) emits ``llm.started`` / ``llm.finished`` /
``warning{code:"llm_failover"}`` events. ``structured()`` never raises:
anything short of a parsed value comes back as ``mode="failed"`` (T5).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import BaseModel

from app.domain.ports import (
    EventPort,
    LLMDelta,
    LLMMessage,
    LLMResponse,
    LLMUsage,
    StructuredResult,
    Tier,
)
from app.domain.tools.base import ToolSpec
from app.harness.llm.base import BaseLLMClient
from app.harness.llm.config import LLMConfig, load_llm_config
from app.harness.llm.failover import FailoverLLM, WarningSink
from app.harness.llm.messages import (
    ThinkStreamSplitter,
    split_content,
    to_langchain,
    tool_calls_of,
    tool_schema,
)
from app.harness.llm.usage import extract_usage
from app.harness.structured.invoke import invoke_structured
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

UsageSink = Callable[[LLMUsage, str], None]  # (usage, purpose)


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


class HarnessLLM:
    def __init__(
        self,
        tiers: dict[Tier, FailoverLLM],
        *,
        emit: EventPort | None = None,
        on_usage: UsageSink | None = None,
        on_warning: WarningSink | None = None,
        node: str | None = None,
    ) -> None:
        self._tiers = tiers
        self._emit_port = emit
        self._on_usage = on_usage
        self._on_warning = on_warning
        self._node = node

    @classmethod
    def from_config(cls, config: LLMConfig, settings: Settings | None = None) -> HarnessLLM:
        from app.harness.llm.factory import create_client

        settings = settings or get_settings()

        def factory(cfg: Any) -> BaseLLMClient:
            return create_client(cfg, settings)

        return cls(
            {tier: FailoverLLM(config.tier(tier).chain(), factory=factory) for tier in ("deep", "quick")}
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> HarnessLLM:
        settings = settings or get_settings()
        return cls.from_config(load_llm_config(settings), settings)

    def bind(
        self,
        *,
        emit: EventPort | None = None,
        on_usage: UsageSink | None = None,
        on_warning: WarningSink | None = None,
        node: str | None = None,
    ) -> HarnessLLM:
        """Same clients, new run/node context for events and usage accounting."""
        return HarnessLLM(
            self._tiers,
            emit=emit or self._emit_port,
            on_usage=on_usage or self._on_usage,
            on_warning=on_warning or self._on_warning,
            node=node or self._node,
        )

    def primary(self, tier: Tier) -> tuple[str, str]:
        cfg = self._tiers[tier].primary
        return cfg.provider, cfg.model

    # -- events -----------------------------------------------------------------

    async def _emit(self, type_: str, payload: dict[str, Any]) -> None:
        if self._emit_port is not None:
            await self._emit_port.emit(type_, payload, node=self._node)

    async def _warn(self, code: str, message: str) -> None:
        await self._emit("warning", {"code": code, "message": message})
        if self._on_warning is not None:
            result = self._on_warning(code, message)
            if inspect.isawaitable(result):
                await result

    async def _started(self, client: BaseLLMClient, tier: Tier, purpose: str) -> None:
        await self._emit(
            "llm.started",
            {"provider": client.provider, "model": client.model, "tier": tier, "purpose": purpose},
        )

    async def _finished(
        self, usage: LLMUsage, duration_ms: int, purpose: str, structured_mode: str | None = None
    ) -> None:
        if self._on_usage is not None:
            self._on_usage(usage, purpose)
        payload: dict[str, Any] = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cost_usd": usage.cost_usd,
            "duration_ms": duration_ms,
        }
        if structured_mode is not None:
            payload["structured_mode"] = structured_mode
        await self._emit("llm.finished", payload)

    # -- LLMPort ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        tier: Tier,
        tools: list[ToolSpec] | None = None,
        purpose: str,
    ) -> LLMResponse:
        lc_messages = to_langchain(messages)
        schemas = [tool_schema(t) for t in tools] if tools else None

        async def op(client: BaseLLMClient) -> tuple[BaseLLMClient, AIMessage, int]:
            await self._started(client, tier, purpose)
            start = time.monotonic()
            msg = await client.ainvoke(lc_messages, tools=schemas)
            return client, msg, _ms(start)

        client, msg, duration = await self._tiers[tier].run(op, on_warning=self._warn)
        text, reasoning = split_content(msg.content)
        usage = extract_usage(msg, client.info)
        await self._finished(usage, duration, purpose)
        return LLMResponse(
            text=text,
            tool_calls=tool_calls_of(msg),
            usage=usage,
            provider=client.provider,
            model=client.model,
            duration_ms=duration,
            reasoning=reasoning or None,
            provider_content=[b if isinstance(b, dict) else {"type": "text", "text": b} for b in msg.content]
            if isinstance(msg.content, list)
            else None,
        )

    async def structured[S: BaseModel](
        self, messages: list[LLMMessage], schema: type[S], *, tier: Tier, purpose: str
    ) -> StructuredResult[S]:
        lc_messages = to_langchain(messages)
        start = time.monotonic()

        async def op(client: BaseLLMClient) -> StructuredResult[S]:
            await self._started(client, tier, purpose)
            return await invoke_structured(client, lc_messages, schema)

        try:
            result = await self._tiers[tier].run(op, on_warning=self._warn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("structured %s failed on every client: %s", schema.__name__, exc)
            await self._warn("structured_failed", f"{schema.__name__}: {exc}")
            result = StructuredResult[S](value=None, raw_text=str(exc), mode="failed")
        await self._finished(result.usage, _ms(start), purpose, structured_mode=result.mode)
        return result

    async def stream(
        self, messages: list[LLMMessage], *, tier: Tier, purpose: str
    ) -> AsyncIterator[LLMDelta]:
        lc_messages = to_langchain(messages)
        start = time.monotonic()
        final: AIMessageChunk | None = None
        last_client: BaseLLMClient | None = None

        async def open_stream(client: BaseLLMClient) -> AsyncIterator[AIMessageChunk]:
            await self._started(client, tier, purpose)
            async for chunk in client.astream(lc_messages):
                yield chunk

        splitter = ThinkStreamSplitter()
        async for client, chunk in self._tiers[tier].stream(open_stream, on_warning=self._warn):
            last_client = client
            final = chunk if final is None else final + chunk
            answer, reasoning = split_content(chunk.content, inline_think=False)
            if reasoning:
                yield LLMDelta(channel="reasoning", text=reasoning)
            for channel, text in splitter.feed(answer):
                yield LLMDelta(channel=channel, text=text)
        for channel, text in splitter.flush():
            yield LLMDelta(channel=channel, text=text)

        usage = extract_usage(final, last_client.info) if final is not None and last_client else LLMUsage()
        await self._finished(usage, _ms(start), purpose)
