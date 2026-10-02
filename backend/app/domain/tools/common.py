"""Helpers shared by the domain tools (symbol parsing, derived provenance records)."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field
from pydantic_core import to_jsonable_python

from app.domain.data.base import Capability, DataRequest, DataResult
from app.domain.data.registry import get_router
from app.domain.models.provenance import ProvenanceRecord, new_record
from app.domain.models.symbol import Market, Symbol, normalize_symbol


def _upper(value: Any) -> Any:
    return value.strip().upper() if isinstance(value, str) else value


# Asset-class hint. Only "TH"/"US" change how a bare stock ticker resolves; the others are accepted
# because crypto / FX / metal tickers are recognised from the symbol itself. Anything else is rejected.
MarketHint = Annotated[Literal["US", "TH", "GLOBAL", "CRYPTO", "FX"], BeforeValidator(_upper)]
# Exchange lists (sector screens) only exist for these two.
ListMarket = Annotated[Literal["US", "TH"], BeforeValidator(_upper)]


class SymbolArgs(BaseModel):
    symbol: str = Field(description="Ticker as the user wrote it, e.g. PTT, AAPL, EURUSD, XAUUSD, BTC")
    market: MarketHint | None = Field(
        default=None, description="Asset class: US, TH, GLOBAL, CRYPTO or FX. Omit when unsure"
    )


def to_symbol(raw: str, market: str | None = None) -> Symbol:
    return normalize_symbol(raw, Market(market) if market in ("US", "TH") else None)


async def fetch(
    cap: Capability, symbol: Symbol | None = None, *, as_of: date | None = None, **params: Any
) -> DataResult:
    """``as_of`` makes the provider serve the window that *ends* on that day (backtest)."""
    return await get_router().get(cap, DataRequest(symbol=symbol, params=params, as_of=as_of))


def derived_record(
    result: DataResult,
    value: Any,
    *,
    tool: str,
    unit: str | None = None,
    as_of: date | datetime | None = None,
    params: dict[str, Any] | None = None,
) -> ProvenanceRecord:
    """A data record holding the numbers a tool shows, linked to the provider's own records.

    Some providers record only a label or a count; the number check (F3) needs
    the actual values, so the tool records what it returns.
    """
    return new_record(
        "data",
        value,
        source=result.provider,
        tool=tool,
        unit=unit,
        as_of=as_of,
        params=params,
        inputs=[r.id for r in result.records],
    )


def jsonable(value: Any) -> Any:
    return to_jsonable_python(value, fallback=str)


def record_ids(records: list[ProvenanceRecord]) -> list[str]:
    return [r.id for r in records]


_NORM_RE = re.compile(r"[^a-z0-9]")


def norm_key(key: str) -> str:
    return _NORM_RE.sub("", key.lower())


def pick(values: dict[str, Any], *candidates: str) -> float | None:
    """First numeric value whose key matches one of ``candidates`` (case/punctuation-insensitive)."""
    normalized = {norm_key(k): v for k, v in values.items()}
    for cand in candidates:
        v = normalized.get(norm_key(cand))
        if isinstance(v, int | float) and not isinstance(v, bool):
            return float(v)
    return None


def benchmark_for(sym: Symbol) -> str | None:
    """``.BK`` → ``^SET.BK``, US stock → ``SPY``; forex/crypto/index have no default benchmark."""
    if sym.market == Market.TH and sym.canonical != "^SET.BK":
        return "^SET.BK"
    if sym.market == Market.US and sym.asset_type.value == "stock":
        return "SPY"
    return None
