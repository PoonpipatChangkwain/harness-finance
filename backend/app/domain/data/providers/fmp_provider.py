"""Financial Modeling Prep — free-tier analyst targets, key metrics, peers.

Ported concept from ``../FinRobot/finrobot/data_source/fmp_utils.py`` and
``../FinRobot/finrobot_equity/core/src/modules/market_data_api.py`` (EV /
market-cap history). FMP sometimes answers an invalid key with HTTP 200 and
an ``{"Error Message": ...}`` body rather than a 401, so that body shape is
checked explicitly and mapped to ``NotConfigured``.
"""

from __future__ import annotations

from typing import Any

from app.domain.data.base import Capability, DataRequest, DataResult, NoData, NotConfigured, Unsupported
from app.domain.data.http import make_client, request_with_retry
from app.domain.data.schemas import FundamentalsSnapshot
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol
from app.settings import get_settings

_BASE_V3 = "https://financialmodelingprep.com/api/v3"
_BASE_V4 = "https://financialmodelingprep.com/api/v4"


def _check_error_body(body: Any) -> None:
    if isinstance(body, dict) and ("Error Message" in body or "error" in body):
        message = body.get("Error Message") or body.get("error")
        if "api key" in str(message).lower() or "limit" in str(message).lower():
            raise NotConfigured(str(message))


class FmpProvider:
    id = "fmp"
    capabilities = frozenset({Capability.ANALYST, Capability.FUNDAMENTALS})

    def is_configured(self) -> bool:
        return bool(get_settings().fmp_api_key)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    def _key(self) -> str:
        key = get_settings().fmp_api_key
        if not key:
            raise NotConfigured("FMP_API_KEY is not set")
        return key.get_secret_value()

    async def _get(self, base: str, path: str, params: dict[str, str]) -> Any:
        async with make_client() as client:
            resp = await request_with_retry(
                client, "GET", f"{base}/{path}", params={**params, "apikey": self._key()}
            )
        body = resp.json()
        _check_error_body(body)
        return body

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported(f"{cap.value} requires a symbol")
        if cap == Capability.ANALYST:
            return await self._analyst(req)
        if cap == Capability.FUNDAMENTALS:
            return await self._fundamentals(req)
        raise Unsupported(f"fmp does not implement {cap.value}")

    async def _analyst(self, req: DataRequest) -> DataResult:
        symbol = req.symbol.canonical  # type: ignore[union-attr]
        consensus = await self._get(_BASE_V4, "price-target-consensus", {"symbol": symbol})
        peers = await self._get(_BASE_V4, "stock_peers", {"symbol": symbol})
        if not consensus:
            raise NoData(f"no analyst price targets for {symbol}")
        row = consensus[0] if isinstance(consensus, list) else consensus
        data = {
            "symbol": symbol,
            "target_high": row.get("targetHigh"),
            "target_low": row.get("targetLow"),
            "target_consensus": row.get("targetConsensus"),
            "target_median": row.get("targetMedian"),
            "peers": (peers[0].get("peersList", []) if isinstance(peers, list) and peers else []),
        }
        record = new_record("data", data, source=self.id, tool="price_target_consensus")
        return DataResult(
            provider=self.id, capability=Capability.ANALYST, data=data, records=[record], status="ok"
        )

    async def _fundamentals(self, req: DataRequest) -> DataResult:
        symbol = req.symbol.canonical  # type: ignore[union-attr]
        key_metrics = await self._get(_BASE_V3, f"key-metrics-ttm/{symbol}", {})
        ratios = await self._get(_BASE_V3, f"ratios-ttm/{symbol}", {})
        if not key_metrics and not ratios:
            raise NoData(f"no key metrics/ratios for {symbol}")
        merged: dict[str, float | None] = {}
        for row in (key_metrics[0] if key_metrics else {}), (ratios[0] if ratios else {}):
            for k, v in row.items():
                if isinstance(v, int | float):
                    merged[k] = float(v)
        snapshot = FundamentalsSnapshot(symbol=symbol, period="ttm", metrics=merged)
        record = new_record("data", {"count": len(merged)}, source=self.id, tool="key_metrics_ratios_ttm")
        return DataResult(
            provider=self.id, capability=Capability.FUNDAMENTALS, data=snapshot, records=[record], status="ok"
        )
