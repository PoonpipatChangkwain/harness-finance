"""Adanos — aggregate retail-sentiment snapshot across Reddit/X/Polymarket.

Ported from ``../FinRobot/finrobot_equity/core/src/modules/
retail_sentiment_client.py``: queries each platform endpoint, tolerates a
platform being down (falls back to "no data" for that source rather than
failing the whole snapshot), and returns one aggregate ``SocialPost``
(``kind="aggregate"``) rather than individual posts.
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Literal

from app.domain.data.base import Capability, DataRequest, DataResult, DataStatus, NotConfigured
from app.domain.data.http import make_client
from app.domain.data.schemas import SocialPost
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol
from app.settings import get_settings

_BASE = "https://api.adanos.org"

_PLATFORM_SPECS = (
    {"key": "reddit", "path": "/reddit/stocks/v1/compare", "activity_field": "mentions"},
    {"key": "x", "path": "/x/stocks/v1/compare", "activity_field": "mentions"},
    {"key": "polymarket", "path": "/polymarket/stocks/v1/compare", "activity_field": "trade_count"},
)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class AdanosProvider:
    id = "adanos"
    capabilities = frozenset({Capability.SOCIAL})

    def is_configured(self) -> bool:
        return bool(get_settings().adanos_api_key)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    def _key(self) -> str:
        key = get_settings().adanos_api_key
        if not key:
            raise NotConfigured("ADANOS_API_KEY is not set")
        return key.get_secret_value()

    async def _fetch_source_row(
        self, client: Any, spec: dict[str, str], ticker: str, days: int
    ) -> dict[str, Any] | None:
        try:
            resp = await client.get(
                f"{_BASE}{spec['path']}",
                params={"tickers": ticker, "days": days},
                headers={"X-API-Key": self._key()},
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return None
        for item in payload.get("stocks", []):
            if str(item.get("ticker", "")).strip().upper().replace("$", "") == ticker:
                return dict(item)
        return None

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise NotConfigured("SOCIAL requires a symbol")
        ticker = req.symbol.canonical.split(".")[0].upper()
        days = int(req.params.get("lookback_days", 7))

        sources: list[dict[str, Any]] = []
        async with make_client() as client:
            for spec in _PLATFORM_SPECS:
                row = await self._fetch_source_row(client, spec, ticker, days)
                buzz = _safe_float(row.get("buzz_score")) if row else 0.0
                bullish = _safe_float(row.get("bullish_pct")) if row else None
                activity = row.get(spec["activity_field"], 0) if row else 0
                has_data = bool((buzz or 0) > 0 or (activity or 0) > 0 or bullish is not None)
                sources.append(
                    {
                        "key": spec["key"],
                        "buzz_score": buzz,
                        "bullish_pct": bullish,
                        "activity": activity,
                        "has_data": has_data,
                    }
                )

        covered = [s for s in sources if s["has_data"]]
        buzz_values: list[float] = [s["buzz_score"] for s in covered if s["buzz_score"] is not None]
        bullish_values: list[float] = [s["bullish_pct"] for s in covered if s["bullish_pct"] is not None]
        average_buzz = round(mean(buzz_values), 1) if buzz_values else None
        bullish_avg = round(mean(bullish_values), 1) if bullish_values else None
        snapshot: dict[str, Any] = {
            "ticker": ticker,
            "coverage": f"{len(covered)}/{len(sources)}",
            "average_buzz": average_buzz,
            "bullish_avg": bullish_avg,
            "sources": sources,
        }
        stance: Literal["bullish", "bearish", "neutral"] | None = None
        if bullish_avg is not None:
            stance = "bullish" if bullish_avg >= 55 else "bearish" if bullish_avg <= 45 else "neutral"

        post = SocialPost(
            platform="adanos",
            id=f"adanos-{ticker}",
            text=f"Retail sentiment aggregate for {ticker}: coverage {snapshot['coverage']}",
            stance_label=stance,
            kind="aggregate",
            metrics={"coverage_sources": len(covered)},
        )
        record = new_record("data", snapshot, source=self.id, tool="retail_sentiment_snapshot")
        status: DataStatus = "ok" if covered else "unavailable"
        return DataResult(
            provider=self.id, capability=Capability.SOCIAL, data=[post], records=[record], status=status
        )
