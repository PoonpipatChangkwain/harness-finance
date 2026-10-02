"""Alpha Vantage — free-tier prices, fundamentals, statements, news sentiment.

Ported concept from ``../TradingAgents/tradingagents/dataflows/vendors/
alpha_vantage/``. The free tier returns HTTP 200 with a ``"Note"`` or
``"Information"`` body instead of a real error status when it throttles a
caller, so those bodies are mapped to ``RateLimited`` explicitly. A
process-wide token bucket (``ratelimit.py``, 5 req/min) is acquired before
every call so this provider rarely needs to discover the limit the hard way.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.domain.data import ratelimit
from app.domain.data.base import (
    Capability,
    DataRequest,
    DataResult,
    DataStatus,
    NoData,
    NotConfigured,
    RateLimited,
    Unsupported,
)
from app.domain.data.http import make_client, request_with_retry
from app.domain.data.schemas import FundamentalsSnapshot, NewsItem, StatementSet
from app.domain.models.market import OHLCVBar, OHLCVFrame
from app.domain.models.provenance import new_record
from app.domain.models.symbol import AssetType, Symbol
from app.settings import get_settings

_BASE = "https://www.alphavantage.co/query"


def _check_throttled(body: dict[str, Any]) -> None:
    note = body.get("Note") or body.get("Information")
    if note and ("rate limit" in str(note).lower() or "frequency" in str(note).lower()):
        raise RateLimited(str(note))


class AlphaVantageProvider:
    id = "alpha_vantage"
    capabilities = frozenset(
        {
            Capability.PRICES,
            Capability.FUNDAMENTALS,
            Capability.STATEMENTS,
            Capability.NEWS,
            Capability.INSIDER,
        }
    )

    def is_configured(self) -> bool:
        return bool(get_settings().alpha_vantage_api_key)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        if cap not in self.capabilities:
            return False
        return not (cap == Capability.PRICES and symbol is not None and symbol.asset_type == AssetType.CRYPTO)

    def _api_key(self) -> str:
        key = get_settings().alpha_vantage_api_key
        if not key:
            raise NotConfigured("ALPHA_VANTAGE_API_KEY is not set")
        return key.get_secret_value()

    async def _get(self, params: dict[str, str]) -> dict[str, Any]:
        await ratelimit.acquire(self.id)
        async with make_client() as client:
            resp = await request_with_retry(
                client, "GET", _BASE, params={**params, "apikey": self._api_key()}
            )
        body: dict[str, Any] = resp.json()
        _check_throttled(body)
        return body

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        handler = {
            Capability.PRICES: self._prices,
            Capability.FUNDAMENTALS: self._fundamentals,
            Capability.STATEMENTS: self._statements,
            Capability.NEWS: self._news,
        }.get(cap)
        if handler is None:
            raise Unsupported(f"alpha_vantage does not implement {cap.value}")
        return await handler(req)

    async def _prices(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("PRICES requires a symbol")
        symbol = req.symbol
        if symbol.asset_type == AssetType.FOREX:
            base, quote = symbol.canonical.rstrip("=X")[:3], symbol.canonical.rstrip("=X")[3:6]
            body = await self._get({"function": "FX_DAILY", "from_symbol": base, "to_symbol": quote})
            series = body.get("Time Series FX (Daily)")
        else:
            body = await self._get(
                {"function": "TIME_SERIES_DAILY_ADJUSTED", "symbol": symbol.canonical, "outputsize": "full"}
            )
            series = body.get("Time Series (Daily)")

        if not series:
            raise NoData(f"no Alpha Vantage price series for {symbol.canonical}")

        bars = []
        for day, row in sorted(series.items()):
            bars.append(
                OHLCVBar(
                    ts=datetime.fromisoformat(day),
                    open=Decimal(row["1. open"]),
                    high=Decimal(row["2. high"]),
                    low=Decimal(row["3. low"]),
                    close=Decimal(row["4. close"]),
                    volume=int(float(row.get("6. volume", 0))),
                )
            )
        frame = OHLCVFrame(symbol=symbol.canonical, interval="1d", bars=bars)
        if req.as_of is not None:
            frame = frame.as_of(req.as_of)
            if not frame.bars:
                raise NoData(f"no bars on or before {req.as_of}")
        record = new_record("data", None, source=self.id, tool="daily_prices", as_of=req.as_of)
        return DataResult(
            provider=self.id, capability=Capability.PRICES, data=frame, records=[record], status="ok"
        )

    async def _fundamentals(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("FUNDAMENTALS requires a symbol")
        body = await self._get({"function": "OVERVIEW", "symbol": req.symbol.canonical})
        if not body or "Symbol" not in body:
            raise NoData(f"no overview for {req.symbol.canonical}")
        keys = [
            "PERatio",
            "PriceToBookRatio",
            "ReturnOnEquityTTM",
            "ProfitMargin",
            "DividendYield",
            "Beta",
            "EPS",
        ]
        metrics = {}
        for k in keys:
            raw = body.get(k)
            try:
                metrics[k] = float(raw) if raw not in (None, "None", "-") else None
            except ValueError:
                metrics[k] = None
        snapshot = FundamentalsSnapshot(symbol=req.symbol.canonical, period="ttm", metrics=metrics)
        record = new_record("data", metrics, source=self.id, tool="overview")
        return DataResult(
            provider=self.id, capability=Capability.FUNDAMENTALS, data=snapshot, records=[record], status="ok"
        )

    async def _statements(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("STATEMENTS requires a symbol")
        income_body = await self._get({"function": "INCOME_STATEMENT", "symbol": req.symbol.canonical})
        reports = income_body.get("annualReports", [])
        if not reports:
            raise NoData(f"no statements for {req.symbol.canonical}")
        latest = reports[0]
        statement = StatementSet(
            symbol=req.symbol.canonical,
            period_type="annual",
            fiscal_period=latest.get("fiscalDateEnding", ""),
            income={
                "revenue": _to_float(latest.get("totalRevenue")),
                "net_income": _to_float(latest.get("netIncome")),
                "gross_profit": _to_float(latest.get("grossProfit")),
            },
            balance={},
            cashflow={},
            fiscal_date=date.fromisoformat(latest["fiscalDateEnding"])
            if latest.get("fiscalDateEnding")
            else None,
        )
        record = new_record("data", statement.fiscal_period, source=self.id, tool="income_statement")
        return DataResult(
            provider=self.id, capability=Capability.STATEMENTS, data=statement, records=[record], status="ok"
        )

    async def _news(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("NEWS requires a symbol")
        body = await self._get({"function": "NEWS_SENTIMENT", "tickers": req.symbol.canonical})
        feed = body.get("feed", [])
        items = []
        for entry in feed:
            published_at = None
            raw_time = entry.get("time_published")
            if raw_time:
                try:
                    published_at = datetime.strptime(raw_time, "%Y%m%dT%H%M%S")
                except ValueError:
                    published_at = None
            items.append(
                NewsItem(
                    id=entry.get("url", ""),
                    title=entry.get("title", ""),
                    summary=entry.get("summary"),
                    url=entry.get("url", ""),
                    source=entry.get("source", "alpha_vantage"),
                    published_at=published_at,
                    tickers=[req.symbol.canonical],
                    sentiment=_to_float(entry.get("overall_sentiment_score")),
                )
            )
        record = new_record("data", {"count": len(items)}, source=self.id, tool="news_sentiment")
        status: DataStatus = "ok" if items else "unavailable"
        return DataResult(
            provider=self.id, capability=Capability.NEWS, data=items, records=[record], status=status
        )


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "None", "-") else None
    except (TypeError, ValueError):
        return None
