"""(P17) ReasoningBank tags derived from symbols (pure).

``market`` ∈ ``SET | US | FX | CRYPTO`` (contracts.md §13.1); an index, a commodity or a symbol
that does not normalize has no market tag (the bank then treats lessons as general).
"""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.models.symbol import AssetType, Market, normalize_symbol


def market_tag(symbol: str) -> str | None:
    raw = symbol.strip()
    if raw.upper().endswith("=X"):  # a canonical forex symbol ("EURUSD=X") does not re-normalize
        raw = raw[:-2]
    try:
        sym = normalize_symbol(raw)
    except ValueError:
        return None
    if sym.asset_type == AssetType.FOREX:
        return "FX"
    if sym.asset_type == AssetType.CRYPTO:
        return "CRYPTO"
    if sym.asset_type == AssetType.STOCK:
        return "SET" if sym.market == Market.TH else "US"
    return None


def market_of(symbols: Iterable[str]) -> str | None:
    """The market of the first symbol that has one (a question is usually about one market)."""
    for s in symbols:
        tag = market_tag(str(s))
        if tag is not None:
            return tag
    return None
