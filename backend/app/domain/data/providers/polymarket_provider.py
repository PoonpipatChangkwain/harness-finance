"""Polymarket Gamma API — live prediction-market probabilities, no key.

Ported from ``../TradingAgents/tradingagents/dataflows/vendors/polymarket.py``:
keeps only open, forward-looking markets (``closed`` false, ``endDate`` in
the future) ranked by traded volume. Polymarket serves only live odds with
no historical vintage, so a request dated in the past is withheld
(``Unsupported``) rather than silently answering with today's odds.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, date, datetime
from typing import Any

from app.domain.data.base import Capability, DataRequest, DataResult, NoData, Unsupported
from app.domain.data.http import make_client, request_with_retry
from app.domain.data.schemas import MarketOutcome, PredictionMarket
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol

_BASE = "https://gamma-api.polymarket.com"
_DEFAULT_LIMIT = 6


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        return list(json.loads(value))
    except (json.JSONDecodeError, TypeError):
        return []


def _is_forward_looking(market: dict[str, Any], now: datetime) -> bool:
    if market.get("closed"):
        return False
    end_date = market.get("endDate")
    if end_date:
        try:
            if datetime.fromisoformat(str(end_date).replace("Z", "+00:00")) < now:
                return False
        except ValueError:
            pass
    return bool(_parse_json_list(market.get("outcomePrices"))) and bool(
        _parse_json_list(market.get("outcomes"))
    )


class PolymarketProvider:
    id = "polymarket"
    capabilities = frozenset({Capability.PREDICTION_MARKETS})

    def is_configured(self) -> bool:
        return True

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        if req.as_of is not None and req.as_of < date.today():
            raise Unsupported("Polymarket serves only live odds; no historical vintage")

        query = str(req.params.get("query", ""))
        limit = int(req.params.get("limit", _DEFAULT_LIMIT))
        params = {"limit": 100, "active": "true"}
        if query:
            params["search"] = query

        async with make_client() as client:
            resp = await request_with_retry(client, "GET", f"{_BASE}/markets", params=params)
            data = resp.json()

        now = datetime.now(UTC)
        markets = [m for m in data if isinstance(m, dict) and _is_forward_looking(m, now)]
        markets.sort(key=lambda m: float(m.get("volume") or 0), reverse=True)
        markets = markets[:limit]
        if not markets:
            raise NoData(f"no open Polymarket markets matching {query!r}")

        items = []
        for m in markets:
            names = _parse_json_list(m.get("outcomes"))
            probs = _parse_json_list(m.get("outcomePrices"))
            outcomes = [MarketOutcome(name=str(n), prob=float(p)) for n, p in zip(names, probs, strict=False)]
            end = m.get("endDate")
            end_date = None
            if end:
                with contextlib.suppress(ValueError):
                    end_date = datetime.fromisoformat(str(end).replace("Z", "+00:00")).date()
            items.append(
                PredictionMarket(
                    question=m.get("question", ""),
                    outcomes=outcomes,
                    volume=float(m.get("volume") or 0),
                    end_date=end_date,
                    url=f"https://polymarket.com/market/{m.get('slug', '')}",
                )
            )
        record = new_record(
            "data", {"count": len(items)}, source=self.id, tool="prediction_markets", params={"query": query}
        )
        return DataResult(
            provider=self.id,
            capability=Capability.PREDICTION_MARKETS,
            data=items,
            records=[record],
            status="ok",
        )
