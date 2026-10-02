from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.data.base import Capability, DataRequest, NoData
from app.domain.data.providers import financial_datasets_provider as mod
from app.domain.models.symbol import normalize_symbol
from tests.domain.data._fake_settings import make_settings


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: make_settings(financial_datasets_api_key="test-key"))


@pytest.mark.asyncio
@respx.mock
async def test_financial_datasets_prices() -> None:
    route = respx.get("https://api.financialdatasets.ai/prices/").mock(
        return_value=httpx.Response(
            200,
            json={
                "prices": [
                    {
                        "time": "2026-09-01T00:00:00",
                        "open": 100,
                        "high": 105,
                        "low": 99,
                        "close": 104,
                        "volume": 1000,
                    }
                ]
            },
        )
    )
    provider = mod.FinancialDatasetsProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.PRICES, req)

    assert route.called
    assert route.calls.last.request.headers["x-api-key"] == "test-key"
    assert len(result.data.bars) == 1


@pytest.mark.asyncio
@respx.mock
async def test_financial_datasets_no_data() -> None:
    respx.get("https://api.financialdatasets.ai/prices/").mock(
        return_value=httpx.Response(200, json={"prices": []})
    )
    provider = mod.FinancialDatasetsProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    with pytest.raises(NoData):
        await provider.fetch(Capability.PRICES, req)


@pytest.mark.asyncio
async def test_financial_datasets_unsupported_for_forex() -> None:
    provider = mod.FinancialDatasetsProvider()
    assert provider.supports(Capability.PRICES, normalize_symbol("EURUSD")) is False


@pytest.mark.asyncio
@respx.mock
async def test_financial_datasets_statements() -> None:
    respx.get("https://api.financialdatasets.ai/financials/").mock(
        return_value=httpx.Response(
            200,
            json={
                "income_statements": [
                    {"report_period": "2025-09-30", "revenue": 400_000_000_000, "net_income": 95_000_000_000}
                ],
                "balance_sheets": [{"total_assets": 350_000_000_000}],
                "cash_flow_statements": [{"operating_cash_flow": 100_000_000_000}],
            },
        )
    )
    provider = mod.FinancialDatasetsProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.STATEMENTS, req)

    assert result.data.income["revenue"] == pytest.approx(400_000_000_000)


@pytest.mark.asyncio
@respx.mock
async def test_financial_datasets_fundamentals() -> None:
    respx.get("https://api.financialdatasets.ai/financial-metrics/snapshot/").mock(
        return_value=httpx.Response(200, json={"snapshot": {"price_to_earnings_ratio": 28.5}})
    )
    provider = mod.FinancialDatasetsProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.FUNDAMENTALS, req)

    assert result.data.metrics["price_to_earnings_ratio"] == pytest.approx(28.5)


@pytest.mark.asyncio
@respx.mock
async def test_financial_datasets_filings_insider_institutional_earnings() -> None:
    respx.get("https://api.financialdatasets.ai/filings/").mock(
        return_value=httpx.Response(200, json={"filings": [{"form_type": "10-K"}]})
    )
    respx.get("https://api.financialdatasets.ai/insider-trades/").mock(
        return_value=httpx.Response(
            200,
            json={
                "insider_trades": [
                    {
                        "name": "Jane Doe",
                        "title": "CFO",
                        "transaction_date": "2026-08-01",
                        "transaction_shares_type": "sale",
                        "transaction_shares": 1000,
                        "transaction_price_per_share": 150.0,
                    }
                ]
            },
        )
    )
    respx.get("https://api.financialdatasets.ai/institutional-holdings/").mock(
        return_value=httpx.Response(200, json={"institutional_ownership": [{"investor": "Vanguard"}]})
    )
    respx.get("https://api.financialdatasets.ai/earnings").mock(
        return_value=httpx.Response(200, json={"earnings": [{"fiscal_period": "Q4 2025"}]})
    )
    provider = mod.FinancialDatasetsProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    filings = await provider.fetch(Capability.FILINGS, req)
    insider = await provider.fetch(Capability.INSIDER, req)
    institutional = await provider.fetch(Capability.INSTITUTIONAL, req)
    earnings = await provider.fetch(Capability.ANALYST, req)

    assert filings.data[0]["form_type"] == "10-K"
    assert insider.data[0].insider_name == "Jane Doe"
    assert institutional.data[0]["investor"] == "Vanguard"
    assert earnings.data[0]["fiscal_period"] == "Q4 2025"
