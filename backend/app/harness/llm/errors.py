"""LLM error taxonomy + classification.

Ported from ``../dexter/src/utils/errors.ts`` (classifyError,
isNonRetryableError and the message-pattern tables). Classification looks
at an HTTP status code first (``status_code`` on openai/anthropic SDK
errors, ``code`` on google-genai errors) and falls back to message
patterns, so it works across every provider SDK without importing them.
"""

from __future__ import annotations

import asyncio
import re
from typing import Literal

ErrorKind = Literal[
    "not_configured",
    "context_overflow",
    "rate_limit",
    "billing",
    "auth",
    "timeout",
    "overloaded",
    "server",
    "connection",
    "bad_request",
    "validation",
    "unknown",
]


class LLMError(Exception):
    """Base for errors raised out of the LLM layer."""

    kind: ErrorKind = "unknown"

    def __init__(self, message: str, *, kind: ErrorKind | None = None, provider: str | None = None) -> None:
        super().__init__(message)
        if kind is not None:
            self.kind = kind
        self.provider = provider


class LLMNotConfigured(LLMError):
    """Provider has no API key / base URL, or its SDK is not installed."""

    kind: ErrorKind = "not_configured"


class LLMUnavailable(LLMError):
    """Every client in a failover chain failed."""

    def __init__(self, message: str, attempts: list[tuple[str, ErrorKind, str]]) -> None:
        super().__init__(message, kind=attempts[-1][1] if attempts else "unknown")
        self.attempts = attempts


_PATTERNS: dict[ErrorKind, list[re.Pattern[str] | str]] = {
    "context_overflow": [
        "context length exceeded",
        "maximum context length",
        "prompt is too long",
        "request_too_large",
        "exceeds model context window",
        "context overflow",
        "exceed context limit",
        "model token limit",
        "input is too long",
    ],
    "rate_limit": [
        re.compile(r"rate[_ ]limit|too many requests|\b429\b", re.IGNORECASE),
        "exceeded your current quota",
        "resource has been exhausted",
        "resource_exhausted",
        "quota exceeded",
        "tokens per minute",
    ],
    "billing": [
        re.compile(r"\b402\b"),
        "payment required",
        "insufficient credits",
        "credit balance",
        "insufficient balance",
    ],
    "auth": [
        re.compile(r"invalid[_ ]?api[_ ]?key", re.IGNORECASE),
        "incorrect api key",
        "invalid x-api-key",
        "authentication",
        "unauthorized",
        "permission denied",
        re.compile(r"\b40[13]\b"),
        "api key not valid",
    ],
    "timeout": ["timeout", "timed out", "deadline exceeded"],
    "overloaded": [
        re.compile(r"overloaded_error", re.IGNORECASE),
        "overloaded",
        "service unavailable",
        "high demand",
        re.compile(r"\b529\b"),
    ],
    "connection": ["connection error", "connection refused", "connecterror", "name or service not known"],
}

_ORDER: tuple[ErrorKind, ...] = (
    "context_overflow",
    "rate_limit",
    "billing",
    "auth",
    "timeout",
    "overloaded",
    "connection",
)


def _status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value < 600:
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _matches(text: str, patterns: list[re.Pattern[str] | str]) -> bool:
    lower = text.lower()
    return any(p.search(text) if isinstance(p, re.Pattern) else p in lower for p in patterns)


def classify_message(text: str) -> ErrorKind:
    if not text:
        return "unknown"
    lower = text.lower()
    for kind in _ORDER:
        if kind == "context_overflow" and ("tokens per minute" in lower or "tpm" in lower):
            continue
        if _matches(text, _PATTERNS[kind]):
            return kind
    return "unknown"


def classify_error(exc: BaseException) -> ErrorKind:
    if isinstance(exc, LLMError):
        return exc.kind
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return "timeout"
    name = type(exc).__name__
    if name in ("ValidationError", "OutputParserException", "JSONDecodeError"):
        return "validation"
    if "Timeout" in name:
        return "timeout"
    if "Connection" in name:
        return "connection"

    message = str(exc)
    by_message = classify_message(message)
    status = _status_of(exc)
    if status is not None:
        if status == 429:
            return "rate_limit"
        if status == 402:
            return "billing"
        if status in (401, 403):
            return "auth"
        if status in (408, 504):
            return "timeout"
        if status == 529 or (status == 503 and by_message == "overloaded"):
            return "overloaded"
        if status >= 500:
            return "overloaded" if by_message == "overloaded" else "server"
        if status == 413 and by_message == "rate_limit":
            # Groq: "Request too large ... tokens per minute (TPM)": over this key's per-minute
            # budget, not the model's window, so another provider can still take it
            return "rate_limit"
        if status == 413 or by_message == "context_overflow":
            return "context_overflow"
        if status >= 400:
            return "bad_request"
    return by_message


_NON_RETRYABLE: frozenset[ErrorKind] = frozenset(
    {"not_configured", "context_overflow", "billing", "auth", "bad_request", "validation"}
)
# Kinds where trying the next provider could help. Validation / bad request /
# context overflow are the request's fault, so another provider would fail too.
_FAILOVER: frozenset[ErrorKind] = frozenset(
    {
        "not_configured",
        "rate_limit",
        "billing",
        "auth",
        "timeout",
        "overloaded",
        "server",
        "connection",
        "unknown",
    }
)


def is_retryable(exc: BaseException) -> bool:
    return classify_error(exc) not in _NON_RETRYABLE


def should_failover(kind: ErrorKind) -> bool:
    return kind in _FAILOVER
