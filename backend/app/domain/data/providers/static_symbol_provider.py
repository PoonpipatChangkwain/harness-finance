"""Local symbol search: SET50, top US large-caps, forex majors, top crypto.

Tried first (data-sources.md §2, SYMBOL_SEARCH order): instant, no
network, and covers the overwhelming majority of what a user actually
types. yfinance search is the failover for anything more obscure.
"""

from __future__ import annotations

import csv
from pathlib import Path

from app.domain.data.base import Capability, DataRequest, DataResult, DataStatus
from app.domain.data.schemas import SymbolMatch
from app.domain.models.provenance import new_record
from app.domain.models.symbol import _CRYPTO_BASES, Market, Symbol, normalize_symbol

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _load_rows(name: str) -> list[dict[str, str]]:
    path = _STATIC_DIR / name
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class StaticSymbolProvider:
    id = "static"
    capabilities = frozenset({Capability.SYMBOL_SEARCH})

    def __init__(self) -> None:
        self._entries: list[tuple[str, str, str | None, Market]] | None = None

    def _load(self) -> list[tuple[str, str, str | None, Market]]:
        if self._entries is not None:
            return self._entries
        entries: list[tuple[str, str, str | None, Market]] = []
        for row in _load_rows("set_symbols.csv"):
            entries.append((row["symbol"], row["name_en"], "SET", Market.TH))
        for row in _load_rows("us_stocks.csv"):
            entries.append((row["symbol"], row["name"], "NASDAQ/NYSE", Market.US))
        for row in _load_rows("forex_majors.csv"):
            entries.append((row["symbol"], row["name"], "FX", Market.GLOBAL))
        for row in _load_rows("commodities.csv"):
            entries.append((row["symbol"], row["name"], "Metals", Market.GLOBAL))
        for base in sorted(_CRYPTO_BASES):
            entries.append((f"{base}USD", f"{base} / US Dollar", "Crypto", Market.GLOBAL))
        self._entries = entries
        return entries

    def is_configured(self) -> bool:
        return True

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        query = str(req.params.get("q", "")).strip().upper()
        matches: list[SymbolMatch] = []
        if query:
            for raw_symbol, name, exchange, hint_market in self._load():
                score = 0.0
                if raw_symbol == query:
                    score = 1.0
                elif raw_symbol.startswith(query):
                    score = 0.8
                elif query in raw_symbol or query in name.upper():
                    score = 0.5
                if score > 0:
                    try:
                        symbol = normalize_symbol(raw_symbol, hint_market=hint_market)
                    except ValueError:
                        continue
                    matches.append(SymbolMatch(symbol=symbol, name=name, exchange=exchange, score=score))
        matches.sort(key=lambda m: m.score, reverse=True)
        matches = matches[: int(req.params.get("limit", 10))]
        record = new_record(
            "data", {"count": len(matches)}, source=self.id, tool="symbol_search", params={"q": query}
        )
        status: DataStatus = "ok" if matches else "unavailable"
        return DataResult(
            provider=self.id,
            capability=Capability.SYMBOL_SEARCH,
            data=matches,
            records=[record],
            status=status,
        )
