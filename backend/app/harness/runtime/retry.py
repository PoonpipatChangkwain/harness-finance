"""Generic async retry with exponential backoff (D5).

Ported from ``withRetry`` in ``../dexter/src/model/llm.ts``: at most
``attempts`` tries, sleeping ``base · 2ⁿ`` seconds between them, and never
retrying an error the classifier marks permanent (auth, invalid request,
context too long). Used by the LLM layer; the data layer keeps its own
``request_with_retry`` because ``app.domain`` may not import the harness.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

Sleep = Callable[[float], Awaitable[None]]


async def with_retry[R](
    fn: Callable[[], Awaitable[R]],
    *,
    retryable: Callable[[BaseException], bool],
    attempts: int = 3,
    base: float = 0.5,
    sleep: Sleep = asyncio.sleep,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> R:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for attempt in range(attempts):
        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not retryable(exc) or attempt == attempts - 1:
                raise
            if on_retry is not None:
                on_retry(attempt + 1, exc)
            await sleep(base * (2**attempt))
    raise AssertionError("unreachable")
