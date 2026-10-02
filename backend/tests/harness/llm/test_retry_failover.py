from __future__ import annotations

from typing import Any

import pytest

from app.harness.llm.config import LLMTierConfig
from app.harness.llm.errors import LLMError, LLMNotConfigured, LLMUnavailable, classify_error, is_retryable
from app.harness.llm.failover import FailoverLLM
from app.harness.runtime.retry import with_retry
from tests.harness.llm.stubs import StatusError

sleeps: list[float] = []


async def _no_sleep(seconds: float) -> None:
    sleeps.append(seconds)


@pytest.fixture(autouse=True)
def _reset_sleeps() -> None:
    sleeps.clear()


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (StatusError(429), "rate_limit"),
        (StatusError(401), "auth"),
        (StatusError(403), "auth"),
        (StatusError(402), "billing"),
        (StatusError(500), "server"),
        (StatusError(529, "overloaded_error"), "overloaded"),
        (StatusError(400, "prompt is too long: 250000 tokens"), "context_overflow"),
        (StatusError(413, "Request too large: tokens per minute (TPM): Limit 8000"), "rate_limit"),
        (StatusError(413, "request_too_large"), "context_overflow"),
        (StatusError(400, "invalid schema"), "bad_request"),
        (TimeoutError(), "timeout"),
        (Exception("Error code: 429 - rate_limit_error"), "rate_limit"),
        (Exception("API key not valid. Please pass a valid API key."), "auth"),
        (Exception("model is overloaded, please retry"), "overloaded"),
        (LLMNotConfigured("x"), "not_configured"),
    ],
)
def test_classify(exc: BaseException, kind: str) -> None:
    assert classify_error(exc) == kind


async def test_retry_429_then_success() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise StatusError(429)
        return "ok"

    assert await with_retry(fn, retryable=is_retryable, sleep=_no_sleep) == "ok"
    assert calls == 3
    assert sleeps == [0.5, 1.0]


async def test_retry_401_fails_immediately() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise StatusError(401)

    with pytest.raises(StatusError):
        await with_retry(fn, retryable=is_retryable, sleep=_no_sleep)
    assert calls == 1
    assert sleeps == []


async def test_retry_gives_up_after_three_attempts() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise StatusError(503)

    with pytest.raises(StatusError):
        await with_retry(fn, retryable=is_retryable, sleep=_no_sleep)
    assert calls == 3


class _Client:
    def __init__(self, label: str) -> None:
        self.label = label


def _chain(
    *labels: str, missing: tuple[str, ...] = (), warnings: list[tuple[str, str]] | None = None
) -> FailoverLLM:
    def factory(cfg: LLMTierConfig) -> Any:
        if cfg.model in missing:
            raise LLMNotConfigured(f"{cfg.model} missing")
        return _Client(cfg.model)

    async def on_warning(code: str, message: str) -> None:
        if warnings is not None:
            warnings.append((code, message))

    configs = [LLMTierConfig(provider="openai", model=label) for label in labels]
    return FailoverLLM(configs, factory=factory, on_warning=on_warning, sleep=_no_sleep)


async def test_failover_on_overloaded_emits_warning() -> None:
    warnings: list[tuple[str, str]] = []
    chain = _chain("first", "second", warnings=warnings)
    seen: list[str] = []

    async def op(client: Any) -> str:
        seen.append(client.label)
        if client.label == "first":
            raise StatusError(529, "overloaded_error")
        return f"answer from {client.label}"

    assert await chain.run(op) == "answer from second"
    assert seen == ["first", "first", "first", "second"]  # retried, then switched
    assert len(warnings) == 1
    assert warnings[0][0] == "llm_failover"
    assert "second" in warnings[0][1]


async def test_rate_limited_client_cools_down_then_returns_first() -> None:
    now = [0.0]
    configs = [LLMTierConfig(provider="openai", model=m) for m in ("first", "second")]
    chain = FailoverLLM(
        configs, factory=lambda cfg: _Client(cfg.model), sleep=_no_sleep, cooldown_s=30, clock=lambda: now[0]
    )
    seen: list[str] = []
    limited = True

    async def op(client: Any) -> str:
        seen.append(client.label)
        if client.label == "first" and limited:
            raise StatusError(429)
        return client.label

    assert await chain.run(op) == "second"
    assert seen == ["first", "first", "first", "second"]
    seen.clear()
    now[0] = 10.0  # still cooling: second is tried first, first is not touched
    assert await chain.run(op) == "second" and seen == ["second"]
    seen.clear()
    now[0], limited = 31.0, False  # cooldown over: back to the configured order
    assert await chain.run(op) == "first" and seen == ["first"]


async def test_cooling_clients_are_still_tried_last() -> None:
    now = [0.0]
    configs = [LLMTierConfig(provider="openai", model=m) for m in ("first", "second")]
    chain = FailoverLLM(
        configs, factory=lambda cfg: _Client(cfg.model), sleep=_no_sleep, clock=lambda: now[0]
    )

    async def overloaded(client: Any) -> str:
        raise StatusError(529, "overloaded_error")

    with pytest.raises(LLMUnavailable):
        await chain.run(overloaded)

    async def ok(client: Any) -> str:
        return client.label

    assert await chain.run(ok) == "first"  # everything cooling: original order, nothing skipped


async def test_failover_skips_unconfigured_provider() -> None:
    warnings: list[tuple[str, str]] = []
    chain = _chain("nokey", "backup", missing=("nokey",), warnings=warnings)

    async def op(client: Any) -> str:
        return client.label

    assert await chain.run(op) == "backup"
    assert warnings and warnings[0][0] == "llm_failover"


async def test_no_failover_on_validation_error() -> None:
    chain = _chain("first", "second")
    seen: list[str] = []

    async def op(client: Any) -> str:
        seen.append(client.label)
        raise StatusError(400, "invalid request: bad tool schema")

    with pytest.raises(LLMError) as info:
        await chain.run(op)
    assert info.value.kind == "bad_request"
    assert seen == ["first"]


async def test_all_clients_fail() -> None:
    chain = _chain("a", "b")

    async def op(client: Any) -> str:
        raise StatusError(500)

    with pytest.raises(LLMUnavailable) as info:
        await chain.run(op)
    assert [label for label, _, _ in info.value.attempts] == ["openai:a", "openai:b"]
