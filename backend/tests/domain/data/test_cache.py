from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.data import cache
from app.domain.data.base import Capability, DataResult
from app.domain.models.provenance import new_record


class _FakeSettings:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "get_settings", lambda: _FakeSettings(tmp_path))


def _result() -> DataResult:
    record = new_record("data", 123.45, source="test_provider", tool="t")
    return DataResult(
        provider="test_provider",
        capability=Capability.QUOTE,
        data={"price": 123.45},
        records=[record],
        status="ok",
    )


def test_cache_miss_returns_none() -> None:
    assert cache.get("test_provider", Capability.QUOTE, {}, None) is None


def test_cache_hit_returns_same_result_and_fetched_at() -> None:
    result = _result()
    cache.set("test_provider", Capability.QUOTE, {"x": 1}, None, result)

    hit = cache.get("test_provider", Capability.QUOTE, {"x": 1}, None)

    assert hit is not None
    assert hit.data == result.data
    assert hit.records[0].fetched_at == result.records[0].fetched_at


def test_cache_key_depends_on_params() -> None:
    result = _result()
    cache.set("test_provider", Capability.QUOTE, {"x": 1}, None, result)

    assert cache.get("test_provider", Capability.QUOTE, {"x": 2}, None) is None


def test_expired_entry_is_a_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _result()
    cache.set("test_provider", Capability.QUOTE, {}, None, result)

    # Force the TTL check to see an already-expired entry.
    monkeypatch.setattr(cache.time, "time", lambda: 9_999_999_999.0)

    assert cache.get("test_provider", Capability.QUOTE, {}, None) is None
