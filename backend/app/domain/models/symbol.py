"""Symbol normalization: map what a user types to a canonical vendor symbol.

Ported from ``../TradingAgents/tradingagents/dataflows/symbols.py``
(``normalize_symbol``, ``safe_ticker_component``), extended with a Thai
market rule (`.BK`) that reference has no equivalent for, since none of the
reference repos target the SET (see docs/data-sources.md).
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

_STATIC_DIR = Path(__file__).resolve().parent.parent / "data" / "static"


class AssetType(StrEnum):
    STOCK = "stock"
    FOREX = "forex"
    CRYPTO = "crypto"
    INDEX = "index"
    COMMODITY = "commodity"


class Market(StrEnum):
    US = "US"
    TH = "TH"
    GLOBAL = "GLOBAL"
    OTHER = "OTHER"


class Symbol(BaseModel, frozen=True):
    raw: str
    canonical: str
    asset_type: AssetType
    market: Market
    currency: str
    display: str


_FOREX_CURRENCIES = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CHF",
        "CAD",
        "AUD",
        "NZD",
        "CNY",
        "CNH",
        "HKD",
        "SGD",
        "SEK",
        "NOK",
        "DKK",
        "PLN",
        "MXN",
        "ZAR",
        "TRY",
        "INR",
        "KRW",
        "BRL",
        "RUB",
        "THB",
    }
)

_CRYPTO_BASES = frozenset({"BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LTC", "BCH", "DOT", "AVAX", "LINK"})

_CRYPTO_QUOTES = ("USDT", "USDC", "USD")

# Spot metals (what an FX/CFD broker quotes as XAUUSD). Yahoo has no spot gold, so the
# ``spot_metals`` provider quotes spot and derives bars from the futures proxy below.
_METAL_ALIASES: dict[str, tuple[str, str]] = {
    "XAUUSD": ("XAUUSD", "USD"),
    "XAU": ("XAUUSD", "USD"),
    "GOLD": ("XAUUSD", "USD"),
    "XAGUSD": ("XAGUSD", "USD"),
    "XAG": ("XAGUSD", "USD"),
    "SILVER": ("XAGUSD", "USD"),
}
SPOT_METAL_PROXY: dict[str, str] = {"XAUUSD": "GC=F", "XAGUSD": "SI=F"}
_METAL_DISPLAY = {"XAUUSD": "XAU/USD (spot)", "XAGUSD": "XAG/USD (spot)"}
# Futures typed explicitly stay futures (COMEX front month, priced above spot by the carry).
_METAL_FUTURES = {"GC=F": "Gold futures (COMEX)", "SI=F": "Silver futures (COMEX)"}

_BROKER_SUFFIXES = ("+", ".M", "-ECN", ".RAW", ".PRO", ".ECN")


def _strip_broker_suffix(s: str) -> str:
    for suffix in _BROKER_SUFFIXES:
        if s.endswith(suffix):
            return s[: -len(suffix)]
    return s


def crypto_base(raw: str) -> str | None:
    """Return the crypto base (e.g. ``BTC``) for a USD/USDT/USDC-quoted symbol."""
    if not raw:
        return None
    compact = raw.strip().upper().rstrip("+").replace("-", "")
    for quote in _CRYPTO_QUOTES:
        if compact.endswith(quote):
            base = compact[: -len(quote)]
            if base in _CRYPTO_BASES:
                return base
    return None


def safe_path_component(value: str, max_len: int = 32) -> str:
    """Validate ``value`` is safe to interpolate into a filesystem path.

    Ported from ``safe_ticker_component``: rejects anything that could
    escape a containing directory (path separators, traversal via dots)
    before a symbol is used as a DB filename or cache key.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"symbol must be a non-empty string, got {value!r}")
    if len(value) > max_len:
        raise ValueError(f"symbol exceeds {max_len} chars: {value!r}")
    if not re.fullmatch(r"[A-Za-z0-9._\-\^=+]+", value):
        raise ValueError(f"symbol contains characters not allowed in a path: {value!r}")
    if set(value) == {"."}:
        raise ValueError(f"symbol cannot consist solely of dots: {value!r}")
    return value


_SET_SYMBOLS: frozenset[str] | None = None


def _set_symbols() -> frozenset[str]:
    global _SET_SYMBOLS
    if _SET_SYMBOLS is None:
        path = _STATIC_DIR / "set_symbols.csv"
        symbols: set[str] = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines()[1:]:
                if not line.strip():
                    continue
                symbols.add(line.split(",")[0].strip().upper())
        _SET_SYMBOLS = frozenset(symbols)
    return _SET_SYMBOLS


def normalize_symbol(raw: str, hint_market: Market | None = None) -> Symbol:
    """Map a user-typed symbol to its canonical vendor (Yahoo-style) symbol.

    Resolution order: metals alias -> crypto -> forex (6-letter ISO pair) ->
    index (``^`` prefix) -> Thai stock (``.BK`` suffix / hint / SET list) ->
    plain US stock (default).
    """
    if not raw or not raw.strip():
        raise ValueError("symbol must be a non-empty string")

    s = _strip_broker_suffix(raw.strip().upper())

    if s in _METAL_ALIASES:
        canonical, currency = _METAL_ALIASES[s]
        return Symbol(
            raw=raw,
            canonical=canonical,
            asset_type=AssetType.COMMODITY,
            market=Market.GLOBAL,
            currency=currency,
            display=_METAL_DISPLAY[canonical],
        )
    if s in _METAL_FUTURES:
        return Symbol(
            raw=raw,
            canonical=s,
            asset_type=AssetType.COMMODITY,
            market=Market.GLOBAL,
            currency="USD",
            display=_METAL_FUTURES[s],
        )

    base = crypto_base(s)
    if base is not None:
        canonical = f"{base}-USD"
        return Symbol(
            raw=raw,
            canonical=canonical,
            asset_type=AssetType.CRYPTO,
            market=Market.GLOBAL,
            currency="USD",
            display=canonical,
        )

    if len(s) == 6 and s[:3] in _FOREX_CURRENCIES and s[3:] in _FOREX_CURRENCIES:
        canonical = f"{s}=X"
        return Symbol(
            raw=raw,
            canonical=canonical,
            asset_type=AssetType.FOREX,
            market=Market.GLOBAL,
            currency=s[3:],
            display=f"{s[:3]}/{s[3:]}",
        )

    if s.startswith("^"):
        market = Market.TH if s in ("^SET", "^SET.BK") else Market.GLOBAL
        canonical_index = "^SET.BK" if market == Market.TH else s
        currency = "THB" if market == Market.TH else "USD"
        return Symbol(
            raw=raw,
            canonical=canonical_index,
            asset_type=AssetType.INDEX,
            market=market,
            currency=currency,
            display=canonical_index,
        )
    base_symbol = s[:-3] if s.endswith(".BK") else s
    is_thai = s.endswith(".BK") or hint_market == Market.TH or base_symbol in _set_symbols()
    if is_thai:
        canonical = base_symbol if base_symbol.endswith(".BK") else f"{base_symbol}.BK"
        return Symbol(
            raw=raw,
            canonical=canonical,
            asset_type=AssetType.STOCK,
            market=Market.TH,
            currency="THB",
            display=f"{base_symbol} (SET)",
        )

    return Symbol(
        raw=raw,
        canonical=s,
        asset_type=AssetType.STOCK,
        market=Market.US if hint_market in (None, Market.US) else hint_market,
        currency="USD",
        display=s,
    )
