"""Finnhub — free-tier company profile, news, basic financials, quote.

Ported concept from ``../FinRobot/finrobot/data_source/finnhub_utils.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

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
from app.domain.data.schemas import CompanyProfile, FundamentalsSnapshot, NewsItem
from app.domain.models.market import Quote
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol
from app.settings import get_settings

_BASE = "https://finnhub.io/api/v1"


class FinnhubProvider:
    id = "finnhub"
    capabilities = frozenset({Capability.PROFILE, Capability.NEWS, Capability.FUNDAMENTALS, Capability.QUOTE})

    def is_configured(self) -> bool:
        return bool(get_settings().finnhub_api_key)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    def _token(self) -> str:
        key = get_settings().finnhub_api_key
        if not key:
            raise NotConfigured("FINNHUB_API_KEY is not set")
        return key.get_secret_value()

    async def _get(self, path: str, params: dict[str, str]) -> Any:
        async with make_client() as client:
            resp = await request_with_retry(
                client, "GET", f"{_BASE}/{path}", params={**params, "token": self._token()}
            )
        if resp.status_code == 429:
            raise RateLimited("finnhub rate limited")
        return resp.json()

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        handler = {
            Capability.PROFILE: self._profile,
            Capability.NEWS: self._news,
            Capability.FUNDAMENTALS: self._fundamentals,
            Capability.QUOTE: self._quote,
        }.get(cap)
        if handler is None:
            raise Unsupported(f"finnhub does not implement {cap.value}")
        return await handler(req)

    async def _profile(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("PROFILE requires a symbol")
        body = await self._get("stock/profile2", {"symbol": req.symbol.canonical})
        if not body or not body.get("name"):
            raise NoData(f"no profile for {req.symbol.canonical}")
        profile = CompanyProfile(
            symbol=req.symbol.canonical,
            name=body["name"],
            industry=body.get("finnhubIndustry"),
            exchange=body.get("exchange"),
            country=body.get("country"),
            market_cap=Decimal(str(body["marketCapitalization"]))
            if body.get("marketCapitalization")
            else None,
        )
        record = new_record("data", profile.name, source=self.id, tool="profile2")
        return DataResult(
            provider=self.id, capability=Capability.PROFILE, data=profile, records=[record], status="ok"
        )

    async def _news(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("NEWS requires a symbol")
        to_date = req.as_of or date.today()
        from_date = to_date - timedelta(days=int(req.params.get("lookback_days", 14)))
        body = await self._get(
            "company-news",
            {"symbol": req.symbol.canonical, "from": from_date.isoformat(), "to": to_date.isoformat()},
        )
        items = [
            NewsItem(
                id=str(e.get("id", e.get("url", ""))),
                title=e.get("headline", ""),
                summary=e.get("summary"),
                url=e.get("url", ""),
                source=e.get("source", "finnhub"),
                published_at=datetime.fromtimestamp(e["datetime"], tz=UTC) if e.get("datetime") else None,
                tickers=[req.symbol.canonical],
            )
            for e in (body if isinstance(body, list) else [])
        ]
        record = new_record(
            "data", {"count": len(items)}, source=self.id, tool="company_news", as_of=req.as_of
        )
        status: DataStatus = "ok" if items else "unavailable"
        return DataResult(
            provider=self.id, capability=Capability.NEWS, data=items, records=[record], status=status
        )

    async def _fundamentals(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("FUNDAMENTALS requires a symbol")
        body = await self._get("stock/metric", {"symbol": req.symbol.canonical, "metric": "all"})
        metric = body.get("metric", {}) if isinstance(body, dict) else {}
        if not metric:
            raise NoData(f"no basic financials for {req.symbol.canonical}")
        keys = [
            "peBasicExclExtraTTM",
            "pbAnnual",
            "roeTTM",
            "roaTTM",
            "netMarginTTM",
            "currentRatioAnnual",
            "beta",
        ]
        metrics = {k: metric.get(k) for k in keys}
        snapshot = FundamentalsSnapshot(symbol=req.symbol.canonical, period="ttm", metrics=metrics)
        record = new_record("data", metrics, source=self.id, tool="basic_financials")
        return DataResult(
            provider=self.id, capability=Capability.FUNDAMENTALS, data=snapshot, records=[record], status="ok"
        )

    async def _quote(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("QUOTE requires a symbol")
        body = await self._get("quote", {"symbol": req.symbol.canonical})
        if not body or body.get("c") in (None, 0):
            raise NoData(f"no quote for {req.symbol.canonical}")
        quote = Quote(
            symbol=req.symbol.canonical,
            price=Decimal(str(body["c"])),
            change=Decimal(str(body["d"])) if body.get("d") is not None else None,
            change_pct=Decimal(str(body["dp"])) / 100 if body.get("dp") is not None else None,
            ts=datetime.fromtimestamp(body["t"], tz=UTC) if body.get("t") else datetime.now(UTC),
            currency=req.symbol.currency,
        )
        record = new_record("data", str(body["c"]), source=self.id, tool="quote")
        return DataResult(
            provider=self.id, capability=Capability.QUOTE, data=quote, records=[record], status="ok"
        )
