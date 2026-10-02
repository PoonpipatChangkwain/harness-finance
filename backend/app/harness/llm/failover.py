"""FailoverLLM: try each client of a tier in order (F4).

Pattern from ``../TradingAgents/tradingagents/dataflows/router.py``
(failover chain) applied to LLMs, with Dexter's ``withRetry`` per client.
Each client gets up to 3 attempts (``500msÂ·2â¿``); the chain moves on when
retries are exhausted or the error is provider-shaped (not configured,
5xx, overloaded, auth, rate limit). Request-shaped errors (validation,
bad request, context too long) raise immediately because another
provider would fail the same way. Every switch reports
``warning{code:"llm_failover"}`` through ``on_warning``.

A client that just failed with a rate limit or overload is put on a short
cooldown and moved to the end of the chain, so later calls do not spend
their retries on a provider that is still refusing (it is still tried if
every client is cooling down).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial
from typing import Any

from app.harness.llm.base import BaseLLMClient
from app.harness.llm.config import LLMTierConfig
from app.harness.llm.errors import (
    ErrorKind,
    LLMError,
    LLMNotConfigured,
    LLMUnavailable,
    classify_error,
    is_retryable,
    should_failover,
)
from app.harness.runtime.retry import Sleep, with_retry

logger = logging.getLogger(__name__)

WarningSink = Callable[[str, str], Awaitable[None] | None]
ClientFactory = Callable[[LLMTierConfig], BaseLLMClient]

COOLDOWN_KINDS: frozenset[ErrorKind] = frozenset({"rate_limit", "overloaded"})
DEFAULT_COOLDOWN_S = 30.0


async def _notify(sink: WarningSink | None, code: str, message: str) -> None:
    if sink is None:
        return
    result = sink(code, message)
    if inspect.isawaitable(result):
        await result


class FailoverLLM:
    def __init__(
        self,
        configs: list[LLMTierConfig],
        *,
        factory: ClientFactory,
        on_warning: WarningSink | None = None,
        attempts: int = 3,
        sleep: Sleep = asyncio.sleep,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not configs:
            raise ValueError("FailoverLLM needs at least one config")
        self.configs = configs
        self._factory = factory
        self._clients: dict[str, BaseLLMClient] = {}
        self.on_warning = on_warning
        self._attempts = attempts
        self._sleep = sleep
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._cooling_until: dict[str, float] = {}

    @property
    def primary(self) -> LLMTierConfig:
        return self.configs[0]

    def _order(self) -> list[LLMTierConfig]:
        """Chain order for this call: clients on cooldown go last, keeping their relative order."""
        now = self._clock()
        ready = [c for c in self.configs if self._cooling_until.get(c.label, 0.0) <= now]
        return ready + [c for c in self.configs if c not in ready]

    def _record_failure(self, cfg: LLMTierConfig, kind: ErrorKind) -> None:
        if kind in COOLDOWN_KINDS and self._cooldown_s > 0:
            self._cooling_until[cfg.label] = self._clock() + self._cooldown_s

    def _client(self, cfg: LLMTierConfig) -> BaseLLMClient:
        client = self._clients.get(cfg.label)
        if client is None:
            client = self._factory(cfg)
            self._clients[cfg.label] = client
        return client

    async def _switch(
        self,
        sink: WarningSink | None,
        chain: list[LLMTierConfig],
        index: int,
        kind: ErrorKind,
        detail: str,
    ) -> None:
        cfg = chain[index]
        self._record_failure(cfg, kind)
        nxt = chain[index + 1].label if index + 1 < len(chain) else None
        message = f"{cfg.label} failed ({kind}: {detail[:200]})" + (f"; switching to {nxt}" if nxt else "")
        logger.warning("llm failover: %s", message)
        if nxt is not None:
            await _notify(sink, "llm_failover", message)

    async def run[R](
        self, op: Callable[[BaseLLMClient], Awaitable[R]], *, on_warning: WarningSink | None = None
    ) -> R:
        sink = on_warning or self.on_warning
        attempts: list[tuple[str, ErrorKind, str]] = []
        chain = self._order()
        for index, cfg in enumerate(chain):
            try:
                client = self._client(cfg)
            except LLMNotConfigured as exc:
                attempts.append((cfg.label, "not_configured", str(exc)))
                await self._switch(sink, chain, index, "not_configured", str(exc))
                continue

            def log_retry(n: int, e: BaseException, label: str = cfg.label) -> None:
                logger.warning("llm retry %d on %s: %s", n, label, classify_error(e))

            try:
                return await with_retry(
                    partial(op, client),
                    retryable=is_retryable,
                    attempts=self._attempts,
                    sleep=self._sleep,
                    on_retry=log_retry,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                kind = classify_error(exc)
                if not should_failover(kind):
                    if isinstance(exc, LLMError):
                        raise
                    raise LLMError(f"{cfg.label}: {exc}", kind=kind, provider=cfg.provider) from exc
                attempts.append((cfg.label, kind, str(exc)))
                await self._switch(sink, chain, index, kind, str(exc))

        tried = ", ".join(f"{label}={kind}" for label, kind, _ in attempts)
        raise LLMUnavailable(f"all LLM clients failed: {tried}", attempts)

    async def stream(
        self,
        open_stream: Callable[[BaseLLMClient], AsyncIterator[Any]],
        *,
        on_warning: WarningSink | None = None,
    ) -> AsyncIterator[tuple[BaseLLMClient, Any]]:
        """Yield ``(client, chunk)``. Fails over only before the first chunk arrives."""
        sink = on_warning or self.on_warning
        attempts: list[tuple[str, ErrorKind, str]] = []
        chain = self._order()
        for index, cfg in enumerate(chain):
            try:
                client = self._client(cfg)
            except LLMNotConfigured as exc:
                attempts.append((cfg.label, "not_configured", str(exc)))
                await self._switch(sink, chain, index, "not_configured", str(exc))
                continue
            started = False
            try:
                async for chunk in open_stream(client):
                    started = True
                    yield client, chunk
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                kind = classify_error(exc)
                if started or not should_failover(kind):
                    raise LLMError(f"{cfg.label}: {exc}", kind=kind, provider=cfg.provider) from exc
                attempts.append((cfg.label, kind, str(exc)))
                await self._switch(sink, chain, index, kind, str(exc))
        tried = ", ".join(f"{label}={kind}" for label, kind, _ in attempts)
        raise LLMUnavailable(f"all LLM clients failed: {tried}", attempts)
