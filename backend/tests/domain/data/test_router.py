from __future__ import annotations

import pytest

from app.domain.data.base import (
    BadRequest,
    Capability,
    DataRequest,
    DataResult,
    NoData,
    NotConfigured,
    Transient,
)
from app.domain.data.router import AllProvidersFailed, DataRouter


class FakeProvider:
    def __init__(self, provider_id: str, cap: Capability, *, configured=True, outcome="ok", data=None):
        self.id = provider_id
        self.capabilities = frozenset({cap})
        self._configured = configured
        self._outcome = outcome
        self._data = data if data is not None else []
        self.calls = 0

    def is_configured(self) -> bool:
        return self._configured

    def supports(self, cap: Capability, symbol) -> bool:
        return cap in self.capabilities

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        self.calls += 1
        if self._outcome == "ok":
            return DataResult(provider=self.id, capability=cap, data=self._data, status="ok")
        if self._outcome == "no_data":
            raise NoData("no data")
        if self._outcome == "transient":
            raise Transient("transient")
        if self._outcome == "bad_request":
            raise BadRequest("bad request")
        if self._outcome == "not_configured":
            raise NotConfigured("not configured")
        raise AssertionError(f"unhandled outcome {self._outcome}")


def _router(*providers: FakeProvider, config: dict) -> DataRouter:
    router = DataRouter()
    for p in providers:
        router.register(p)
    router._config = config  # test-only: bypass routing.yaml
    return router


@pytest.mark.asyncio
async def test_failover_tries_in_order_and_stops_at_first_ok() -> None:
    a = FakeProvider("a", Capability.NEWS, outcome="no_data")
    b = FakeProvider("b", Capability.NEWS, outcome="ok", data=["item"])
    c = FakeProvider("c", Capability.NEWS, outcome="ok", data=["should not be called"])
    router = _router(a, b, c, config={"news": {"mode": "failover", "order": ["a", "b", "c"]}})

    result = await router.get(Capability.NEWS, DataRequest())

    assert result.provider == "b"
    assert result.status == "ok"
    assert c.calls == 0


@pytest.mark.asyncio
async def test_failover_skips_not_configured_without_counting_as_failure() -> None:
    a = FakeProvider("a", Capability.NEWS, configured=False)
    b = FakeProvider("b", Capability.NEWS, outcome="ok", data=["item"])
    router = _router(a, b, config={"news": {"mode": "failover", "order": ["a", "b"]}})

    result = await router.get(Capability.NEWS, DataRequest())

    assert result.provider == "b"
    assert a.calls == 0  # never even called fetch()


@pytest.mark.asyncio
async def test_bad_request_stops_immediately_without_failover() -> None:
    a = FakeProvider("a", Capability.NEWS, outcome="bad_request")
    b = FakeProvider("b", Capability.NEWS, outcome="ok", data=["item"])
    router = _router(a, b, config={"news": {"mode": "failover", "order": ["a", "b"]}})

    with pytest.raises(BadRequest):
        await router.get(Capability.NEWS, DataRequest())
    assert b.calls == 0


@pytest.mark.asyncio
async def test_fanout_merges_ok_results_and_reports_partial() -> None:
    a = FakeProvider("a", Capability.SOCIAL, outcome="ok", data=["post-a"])
    b = FakeProvider("b", Capability.SOCIAL, outcome="transient")
    c = FakeProvider("c", Capability.SOCIAL, outcome="ok", data=["post-c"])
    router = _router(a, b, c, config={"social": {"mode": "fanout", "order": ["a", "b", "c"]}})

    result = await router.get(Capability.SOCIAL, DataRequest())

    assert result.status == "partial"
    assert set(result.data) == {"post-a", "post-c"}


@pytest.mark.asyncio
async def test_core_capability_all_fail_raises() -> None:
    a = FakeProvider("a", Capability.PRICES, outcome="transient")
    b = FakeProvider("b", Capability.PRICES, outcome="no_data")
    router = _router(a, b, config={"prices": {"mode": "failover", "order": ["a", "b"]}})

    with pytest.raises(AllProvidersFailed):
        await router.get(Capability.PRICES, DataRequest())


@pytest.mark.asyncio
async def test_optional_capability_all_fail_returns_unavailable() -> None:
    a = FakeProvider("a", Capability.MACRO, outcome="transient")
    router = _router(a, config={"macro": {"mode": "failover", "order": ["a"]}})

    result = await router.get(Capability.MACRO, DataRequest())

    assert result.status == "unavailable"
