from __future__ import annotations

import time

import pytest

from app.domain.data.ratelimit import TokenBucket, bucket_for


@pytest.mark.asyncio
async def test_token_bucket_allows_burst_up_to_capacity() -> None:
    bucket = TokenBucket(capacity=3, refill_seconds=60)
    start = time.monotonic()
    for _ in range(3):
        await bucket.acquire()
    assert time.monotonic() - start < 0.1


@pytest.mark.asyncio
async def test_token_bucket_blocks_past_capacity() -> None:
    bucket = TokenBucket(capacity=1, refill_seconds=0.2)
    await bucket.acquire()
    start = time.monotonic()
    await bucket.acquire()
    assert time.monotonic() - start >= 0.15


def test_bucket_for_unknown_provider_is_none() -> None:
    assert bucket_for("some_unlimited_provider") is None


def test_bucket_for_alpha_vantage_has_a_bucket() -> None:
    assert bucket_for("alpha_vantage") is not None
