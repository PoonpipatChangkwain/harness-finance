"""Spot gold / silver (XAUUSD, XAGUSD) — what an FX/CFD broker quotes, no key.

Yahoo only lists the COMEX futures (``GC=F``, ``SI=F``), which trade above spot by the
carry (tens of dollars for gold), so a chart or entry on futures does not match the
broker. This provider:

- QUOTE: spot mid of Swissquote's public bid/ask feed, falling back to gold-api.com;
- PRICES: the futures bars (via yfinance) shifted by today's basis
  ``futures last - spot now``. A constant shift leaves ATR, RSI, MACD, Bollinger width
  and every price *difference* unchanged and puts the levels where the broker shows them.
  The basis drifts slowly (carry decays, and jumps at a contract roll), so levels far back
  in a long history are approximate — recorded in the provenance params and notes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.domain.data.base import (
    Capability,
    DataError,
    DataRequest,
    DataResult,
    NoData,
    Transient,
    Unsupported,
)
from app.domain.data.http import make_client, request_with_retry
from app.domain.data.providers.yfinance_provider import YFinanceProvider
from app.domain.models.market import OHLCVBar, OHLCVFrame, Quote
from app.domain.models.provenance import new_record
from app.domain.models.symbol import SPOT_METAL_PROXY, Symbol, normalize_symbol

_SWISSQUOTE = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/{base}/USD"
_GOLD_API = "https://api.gold-api.com/price/{base}"


def swissquote_mid(payload: Any) -> tuple[Decimal, datetime]:
    """Mid of the first platform's 'prime' (else first) spread profile, and its timestamp."""
    if not isinstance(payload, list) or not payload:
        raise Transient("unexpected Swissquote payload")
    first = payload[0]
    profiles = first.get("spreadProfilePrices") or []
    chosen = next(
        (p for p in profiles if p.get("spreadProfile") == "prime"), profiles[0] if profiles else None
    )
    if not chosen or chosen.get("bid") is None or chosen.get("ask") is None:
        raise NoData("no bid/ask in Swissquote payload")
    mid = (Decimal(str(chosen["bid"])) + Decimal(str(chosen["ask"]))) / 2
    ts = datetime.fromtimestamp(int(first.get("ts", 0)) / 1000, UTC) if first.get("ts") else datetime.now(UTC)
    return mid.quantize(Decimal("0.001")), ts


def shift_frame(frame: OHLCVFrame, basis: Decimal, canonical: str) -> OHLCVFrame:
    bars = [
        OHLCVBar(
            ts=b.ts,
            open=b.open - basis,
            high=b.high - basis,
            low=b.low - basis,
            close=b.close - basis,
            volume=b.volume,
        )
        for b in frame.bars
    ]
    return frame.model_copy(update={"symbol": canonical, "bars": bars})


class SpotMetalsProvider:
    id = "spot_metals"
    capabilities = frozenset({Capability.PRICES, Capability.QUOTE})

    def __init__(self, futures: YFinanceProvider | None = None) -> None:
        self._futures = futures or YFinanceProvider()

    def is_configured(self) -> bool:
        return True

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities and symbol is not None and symbol.canonical in SPOT_METAL_PROXY

    async def spot(self, symbol: Symbol) -> tuple[Decimal, datetime, str]:
        base = symbol.canonical[:3]
        errors: list[str] = []
        async with make_client() as client:
            try:
                resp = await request_with_retry(client, "GET", _SWISSQUOTE.format(base=base))
                mid, ts = swissquote_mid(resp.json())
                return mid, ts, "swissquote"
            except (DataError, ValueError) as exc:
                errors.append(f"swissquote: {exc}")
            try:
                resp = await request_with_retry(client, "GET", _GOLD_API.format(base=base))
                body = resp.json()
                price = Decimal(str(body["price"]))
                ts = datetime.fromisoformat(str(body.get("updatedAt", "")).replace("Z", "+00:00"))
                return price, ts, "gold-api"
            except (DataError, ValueError, KeyError) as exc:
                errors.append(f"gold-api: {exc}")
        raise Transient("no spot price: " + "; ".join(errors))

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        if req.symbol is None or req.symbol.canonical not in SPOT_METAL_PROXY:
            raise Unsupported("spot_metals serves XAUUSD / XAGUSD only")
        sym = req.symbol
        if cap == Capability.QUOTE:
            price, ts, source = await self.spot(sym)
            quote = Quote(symbol=sym.canonical, price=price, ts=ts, currency="USD")
            record = new_record("data", str(price), source=source, tool="quote", unit="USD")
            return DataResult(provider=self.id, capability=cap, data=quote, records=[record], status="ok")

        proxy = normalize_symbol(SPOT_METAL_PROXY[sym.canonical])
        fut = await self._futures.fetch(Capability.PRICES, req.model_copy(update={"symbol": proxy}))
        frame: OHLCVFrame = fut.data
        if not frame.bars:
            raise NoData(f"no {proxy.canonical} bars to derive {sym.canonical} from")
        spot, spot_ts, source = await self.spot(sym)
        # basis against the latest futures print (a live 1m close when available)
        fut_last = await self._futures_last(proxy, frame)
        basis = (fut_last - spot).quantize(Decimal("0.001"))
        notes = [
            f"{sym.canonical} bars = {proxy.canonical} futures - basis {basis} (futures {fut_last} vs spot "
            f"{spot} from {source} at {spot_ts.isoformat()}); older levels are approximate"
        ]
        if req.as_of is not None and req.as_of < date.today():
            notes.append("backtest: today's basis applied to past bars")
        record = new_record(
            "data",
            None,
            source=f"{fut.provider}+{source}",
            tool="ohlcv",
            params={**req.params, "proxy": proxy.canonical, "basis": str(basis)},
            as_of=req.as_of,
            inputs=[r.id for r in fut.records],
        )
        return DataResult(
            provider=self.id,
            capability=cap,
            data=shift_frame(frame, basis, sym.canonical),
            records=[*fut.records, record],
            status="ok",
            notes=notes,
        )

    async def _futures_last(self, proxy: Symbol, frame: OHLCVFrame) -> Decimal:
        try:
            live = await self._futures.fetch(Capability.QUOTE, DataRequest(symbol=proxy))
            return Decimal(str(live.data.price))
        except DataError:
            return frame.bars[-1].close
