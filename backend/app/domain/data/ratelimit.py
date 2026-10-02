"""Process-wide token bucket rate limiter, one bucket per provider.

Some Tier B free tiers cap requests hard enough (Alpha Vantage: 5/min) that
even respecting `Retry-After` on 429 wastes calls; a bucket a provider
acquires *before* calling out avoids tripping the limit in the first place.
"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, capacity: int, refill_seconds: float) -> None:
        self._capacity = capacity
        self._refill_seconds = refill_seconds
        self._tokens = float(capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated
                self._tokens = min(
                    self._capacity, self._tokens + elapsed * (self._capacity / self._refill_seconds)
                )
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) * (self._refill_seconds / self._capacity)
                await asyncio.sleep(wait)


_BUCKETS: dict[str, TokenBucket] = {}
_LIMITS: dict[str, tuple[int, float]] = {
    "alpha_vantage": (5, 60.0),
}


def bucket_for(provider_id: str) -> TokenBucket | None:
    if provider_id not in _LIMITS:
        return None
    if provider_id not in _BUCKETS:
        capacity, refill = _LIMITS[provider_id]
        _BUCKETS[provider_id] = TokenBucket(capacity, refill)
    return _BUCKETS[provider_id]


async def acquire(provider_id: str) -> None:
    bucket = bucket_for(provider_id)
    if bucket is not None:
        await bucket.acquire()
