from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.data.base import BadRequest, Capability, DataRequest
from app.domain.data.providers import sec_api_provider as mod
from tests.domain.data._fake_settings import make_settings


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: make_settings(sec_api_key="test-key"))


@pytest.mark.asyncio
@respx.mock
async def test_sec_api_extractor_returns_text() -> None:
    respx.get("https://api.sec-api.io/extractor").mock(
        return_value=httpx.Response(200, text="Risk factors include...")
    )
    provider = mod.SecApiProvider()
    req = DataRequest(
        params={"filing_url": "https://www.sec.gov/Archives/edgar/data/1/filing.htm", "item": "1A"}
    )

    result = await provider.fetch(Capability.FILINGS, req)

    assert result.data["text"] == "Risk factors include..."


@pytest.mark.asyncio
async def test_sec_api_requires_filing_url() -> None:
    provider = mod.SecApiProvider()
    with pytest.raises(BadRequest):
        await provider.fetch(Capability.FILINGS, DataRequest())
