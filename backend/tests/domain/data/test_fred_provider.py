from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.data.base import Capability, DataRequest, NotConfigured
from app.domain.data.providers import fred_provider as mod


class _Secret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class _FakeSettings:
    def __init__(self, key: str | None) -> None:
        self.fred_api_key = _Secret(key) if key else None


@pytest.mark.asyncio
@respx.mock
async def test_fred_returns_macro_series(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: _FakeSettings("test-key"))
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=httpx.Response(
            200,
            json={
                "observations": [
                    {"date": "2026-08-01", "value": "4.10"},
                    {"date": "2026-09-01", "value": "4.25"},
                ]
            },
        )
    )
    provider = mod.FredProvider()

    result = await provider.fetch(Capability.MACRO, DataRequest(params={"series_id": "10y_treasury"}))

    assert result.status == "ok"
    assert result.data.series_id == "DGS10"
    assert len(result.data.observations) == 2
    assert result.data.pct_change == pytest.approx(4.25 / 4.10 - 1.0)


@pytest.mark.asyncio
async def test_fred_not_configured_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: _FakeSettings(None))
    provider = mod.FredProvider()
    with pytest.raises(NotConfigured):
        await provider.fetch(Capability.MACRO, DataRequest(params={"series_id": "cpi"}))


@pytest.mark.asyncio
@respx.mock
async def test_fred_api_key_never_leaks_into_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "SUPER-SECRET-FRED-KEY-VALUE"
    monkeypatch.setattr(mod, "get_settings", lambda: _FakeSettings(secret))
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=httpx.Response(200, json={"observations": [{"date": "2026-09-01", "value": "4.1"}]})
    )
    provider = mod.FredProvider()

    result = await provider.fetch(Capability.MACRO, DataRequest(params={"series_id": "cpi"}))

    dumped = result.model_dump_json()
    assert secret not in dumped
    for record in result.records:
        assert secret not in record.model_dump_json()


def test_fred_resolve_series_id_alias_and_raw() -> None:
    assert mod._resolve_series_id("cpi") == "CPIAUCSL"
    assert mod._resolve_series_id("10Y_Treasury") == "DGS10"
    assert mod._resolve_series_id("CPIAUCSL") == "CPIAUCSL"
