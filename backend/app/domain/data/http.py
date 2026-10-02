"""Shared HTTP client + retry helper for data providers.

Ported pattern from ``../TradingAgents/tradingagents/dataflows/net.py``:
retries only ``Transient``/``RateLimited`` conditions (timeout, 5xx,
connection errors, 429) with exponential backoff + jitter, respects a
vendor's ``Retry-After`` header (capped at 30s), and maps everything else
into the shared error taxonomy so the router can react by behavior.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx

from app.domain.data.base import BadRequest, DataError, NotConfigured, RateLimited, Transient
from app.settings import get_settings

_MAX_RETRIES = 3
_BASE_DELAY = 0.5
_MAX_RETRY_AFTER = 30.0

StatusMapper = Callable[[httpx.Response], DataError | None]


def make_client(**kwargs: object) -> httpx.AsyncClient:
    settings = get_settings()
    headers = {"User-Agent": settings.http_user_agent}
    extra_headers = kwargs.pop("headers", None)
    if isinstance(extra_headers, dict):
        headers.update(extra_headers)
    return httpx.AsyncClient(timeout=20.0, headers=headers, **kwargs)  # type: ignore[arg-type]


def default_status_mapper(resp: httpx.Response) -> DataError | None:
    if resp.status_code == 429:
        return RateLimited(retry_after=_parse_retry_after(resp))
    if resp.status_code in (401, 403):
        return NotConfigured(f"unauthorized ({resp.status_code})")
    if resp.status_code >= 500:
        return Transient(f"server error {resp.status_code}")
    if resp.status_code >= 400:
        return BadRequest(f"bad request {resp.status_code}")
    return None


def _parse_retry_after(resp: httpx.Response) -> float | None:
    header = resp.headers.get("Retry-After")
    if not header:
        return None
    try:
        return min(float(header), _MAX_RETRY_AFTER)
    except ValueError:
        return None


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    status_mapper: StatusMapper = default_status_mapper,
    **kwargs: object,
) -> httpx.Response:
    """Issue a request, retrying transient failures with backoff + jitter."""
    last_error: DataError | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await client.request(method, url, **kwargs)  # type: ignore[arg-type]
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            last_error = Transient(str(exc))
        else:
            error = status_mapper(resp)
            if error is None:
                return resp
            if not error.retryable:
                raise error
            last_error = error

        if attempt >= _MAX_RETRIES:
            break
        delay = _BASE_DELAY * (2**attempt) + random.uniform(0, 0.25)
        if isinstance(last_error, RateLimited) and last_error.retry_after:
            delay = min(last_error.retry_after, _MAX_RETRY_AFTER)
        await asyncio.sleep(delay)

    assert last_error is not None
    raise last_error


async def to_thread_retry(fn: Callable[[], object], *, retries: int = 2) -> object:
    """Run a sync SDK call (e.g. yfinance) in a thread, retrying transient errors."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await asyncio.to_thread(fn)
        except Exception as exc:
            last_exc = exc
            if attempt >= retries:
                break
            await asyncio.sleep(_BASE_DELAY * (2**attempt))
    raise Transient(str(last_exc)) from last_exc


AsyncFn = Callable[[], Awaitable[object]]
