from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.data import http as http_module
from app.domain.data.base import Capability, DataRequest
from app.domain.data.providers import financial_datasets_provider as fd_mod
from app.domain.data.providers import yfinance_provider as yf_mod
from app.domain.data.registry import get_router
from app.domain.models.symbol import normalize_symbol
from tests.domain.data._fake_settings import make_settings


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(http_module.asyncio, "sleep", _instant_sleep)


class _BrokenTicker:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, period: str, interval: str, auto_adjust: bool) -> None:
        raise ConnectionError("simulated yfinance outage")


@pytest.mark.asyncio
@respx.mock
async def test_prices_fails_over_from_yfinance_to_financial_datasets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yf_mod.yf, "Ticker", _BrokenTicker)
    monkeypatch.setattr(fd_mod, "get_settings", lambda: make_settings(financial_datasets_api_key="test-key"))
    respx.get("https://api.financialdatasets.ai/prices/").mock(
        return_value=httpx.Response(
            200,
            json={
                "prices": [
                    {
                        "time": "2026-09-01T00:00:00",
                        "open": 1,
                        "high": 2,
                        "low": 0.5,
                        "close": 1.5,
                        "volume": 10,
                    }
                ]
            },
        )
    )

    router = get_router()
    result = await router.get(Capability.PRICES, DataRequest(symbol=normalize_symbol("AAPL")))

    assert result.provider == "financial_datasets"
    assert result.status == "ok"
