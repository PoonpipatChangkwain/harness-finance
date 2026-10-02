"""Market data a portfolio needs: quotes, 1-year history, FX into the base currency, sectors.

Everything goes through the data router (F4) — ``data_source()`` is looked up at
call time so tests can swap in a fixture source. A failed fetch becomes a
``None`` plus a warning, never an exception: one bad symbol must not sink the
whole book's analytics (D5 partial failure).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import cache
from typing import Protocol

from app.domain.data.base import Capability, DataRequest, DataResult
from app.domain.data.providers.static_symbol_provider import _load_rows
from app.domain.models.market import OHLCVFrame, Quote
from app.domain.models.provenance import ProvenanceRecord, new_record
from app.domain.models.symbol import AssetType, Market, Symbol, normalize_symbol


class MarketData(Protocol):
    async def get(self, cap: Capability, req: DataRequest) -> DataResult: ...


def data_source() -> MarketData:
    from app.domain.data.registry import get_router

    return get_router()


@dataclass
class PriceQuote:
    symbol: str
    price: Decimal
    prev_close: Decimal | None
    currency: str
    as_of: date | None
    provider: str
    record: ProvenanceRecord
    records: list[ProvenanceRecord] = field(default_factory=list)


@dataclass
class History:
    symbol: str
    closes: dict[date, float]
    provider: str
    record: ProvenanceRecord
    records: list[ProvenanceRecord] = field(default_factory=list)


def fx_symbol(ccy: str, base: str) -> str:
    """Yahoo-style pair that prices one ``ccy`` in ``base`` (``USD``→``THB`` = ``USDTHB=X``)."""
    return f"{ccy}{base}=X"


def fx_pair(ccy: str, base: str) -> Symbol:
    """The FX ``Symbol`` whose quote converts ``ccy`` into ``base`` (built directly, so any
    currency code works, not only the majors ``normalize_symbol`` knows)."""
    canonical = fx_symbol(ccy, base)
    return Symbol(
        raw=canonical,
        canonical=canonical,
        asset_type=AssetType.FOREX,
        market=Market.GLOBAL,
        currency=base,
        display=f"{ccy}/{base}",
    )


async def fetch_quote(sym: Symbol, data: MarketData) -> tuple[PriceQuote | None, str | None]:
    """Latest price + previous close, or (None, reason)."""
    try:
        result = await data.get(Capability.QUOTE, DataRequest(symbol=sym))
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    quote: Quote | None = result.data if result.status != "unavailable" else None
    if quote is None or quote.price is None or quote.price <= 0:
        return None, f"no quote ({result.status})"
    prev = quote.price - quote.change if quote.change is not None else None
    as_of = quote.ts.date() if quote.ts else None
    value = {"price": str(quote.price), "prev_close": None if prev is None else str(prev)}
    record = new_record(
        "data",
        value,
        source=result.provider,
        tool="portfolio.quote",
        unit=quote.currency or sym.currency,
        as_of=as_of,
        params={"symbol": sym.canonical},
        inputs=[r.id for r in result.records],
    )
    return (
        PriceQuote(
            symbol=sym.canonical,
            price=quote.price,
            prev_close=prev,
            currency=quote.currency or sym.currency,
            as_of=as_of,
            provider=result.provider,
            record=record,
            records=[*result.records, record],
        ),
        None,
    )


async def fetch_history(
    sym: Symbol, data: MarketData, *, range_: str = "1y", as_of: date | None = None
) -> tuple[History | None, str | None]:
    """Daily closes keyed by date (cut at ``as_of``), or (None, reason)."""
    req = DataRequest(symbol=sym, params={"range": range_, "interval": "1d"}, as_of=as_of)
    try:
        result = await data.get(Capability.PRICES, req)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    frame: OHLCVFrame | None = result.data
    if frame is None or not frame.bars:
        return None, f"no price history ({result.status})"
    closes = {b.ts.date(): float(b.close) for b in frame.bars if as_of is None or b.ts.date() <= as_of}
    if not closes:
        return None, "no bars before as_of"
    last = max(closes)
    record = new_record(
        "data",
        {
            "symbol": sym.canonical,
            "bars": len(closes),
            "first": min(closes).isoformat(),
            "last_close": closes[last],
        },
        source=result.provider,
        tool="portfolio.history",
        unit=sym.currency,
        as_of=last,
        params={"symbol": sym.canonical, "range": range_},
        inputs=[r.id for r in result.records],
    )
    return (
        History(
            symbol=sym.canonical,
            closes=closes,
            provider=result.provider,
            record=record,
            records=[*result.records, record],
        ),
        None,
    )


@cache
def _static_sectors() -> dict[str, str]:
    out: dict[str, str] = {}
    for row in _load_rows("set_symbols.csv"):
        out[f"{row['symbol'].strip().upper()}.BK"] = row.get("sector") or ""
    for row in _load_rows("us_stocks.csv"):
        out[row["symbol"].strip().upper()] = row.get("sector") or ""
    return {k: v for k, v in out.items() if v}


_ASSET_SECTORS = {
    AssetType.FOREX: "Currency",
    AssetType.CRYPTO: "Crypto",
    AssetType.COMMODITY: "Commodity",
    AssetType.INDEX: "Index",
}


async def sector_of(sym: Symbol, data: MarketData) -> str:
    """Sector from the static SET/US lists, else the PROFILE capability, else ``"Unknown"``."""
    if sym.asset_type in _ASSET_SECTORS:
        return _ASSET_SECTORS[sym.asset_type]
    static = _static_sectors().get(sym.canonical)
    if static:
        return static
    try:
        result = await data.get(Capability.PROFILE, DataRequest(symbol=sym))
        sector = getattr(result.data, "sector", None)
    except Exception:
        sector = None
    return str(sector) if sector else "Unknown"


def benchmark_symbol(base_currency: str) -> Symbol | None:
    """``^SET.BK`` for a THB book, ``SPY`` for USD; None otherwise."""
    if base_currency == "THB":
        return normalize_symbol("^SET.BK")
    if base_currency == "USD":
        return normalize_symbol("SPY", Market.US)
    return None


def lot_size(sym: Symbol, lot_size_th: int = 100) -> int:
    """Whole-lot trading unit used by the rebalance: SET board lot, otherwise 1."""
    return lot_size_th if sym.asset_type == AssetType.STOCK and sym.market == Market.TH else 1
