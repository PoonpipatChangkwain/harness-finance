from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.domain.data import providers as _providers  # noqa: F401
from app.domain.data.base import Capability, DataRequest, NotConfigured, Unsupported
from app.domain.data.providers import sec_edgar_provider as mod
from app.domain.models.symbol import normalize_symbol

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "providers" / "sec_edgar"


class _FakeSettings:
    def __init__(self, cache_dir: Path, user_agent: str | None):
        self.cache_dir = cache_dir
        self.sec_edgar_user_agent = user_agent


@pytest.fixture(autouse=True)
def _settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: _FakeSettings(tmp_path, "Test test@example.com"))


@pytest.mark.asyncio
@respx.mock
async def test_sec_edgar_statements_point_in_time() -> None:
    respx.get(mod._TICKERS_URL).mock(
        return_value=httpx.Response(200, json=json.loads((FIXTURES / "company_tickers.json").read_text()))
    )
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json").mock(
        return_value=httpx.Response(200, json=json.loads((FIXTURES / "company_facts.json").read_text()))
    )
    provider = mod.SecEdgarProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.STATEMENTS, req)

    assert result.status == "ok"
    assert result.data.fiscal_period == "2025-09-30"
    assert result.data.income["revenue"] == pytest.approx(400_000_000_000)
    assert result.data.balance["total_assets"] == pytest.approx(350_000_000_000)


@pytest.mark.asyncio
async def test_sec_edgar_not_configured_without_user_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: _FakeSettings(tmp_path, None))
    provider = mod.SecEdgarProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    with pytest.raises(NotConfigured):
        await provider.fetch(Capability.STATEMENTS, req)


@pytest.mark.asyncio
async def test_sec_edgar_thai_symbol_unsupported() -> None:
    provider = mod.SecEdgarProvider()
    req = DataRequest(symbol=normalize_symbol("PTT"))

    with pytest.raises(Unsupported):
        await provider.fetch(Capability.STATEMENTS, req)
