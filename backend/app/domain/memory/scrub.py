"""(P17) Deterministic privacy scrubber for ReasoningBank items (research §C.5 step 2).

``scrub_text(text) -> (clean, hits)`` replaces, in this order:

- PII (the item is dropped by the bank when any of these hit): e-mail, Thai national ID
  (13 digits, with or without dashes), phone numbers (Thai mobile/landline, +66), bank account
  numbers (``xxx-x-xxxxx-x`` or 10-12 bare digits);
- money amounts → a coarse bucket (THB: ``<THB 100k`` / ``THB 100k-1m`` / ``>THB 1m``; USD:
  ``<USD 3k`` / ``USD 3k-30k`` / ``>USD 30k``), share/unit counts → ``a position``;
- percentages → the nearest multiple of 5 (below 2.5% → ``<5%``, so "risk 1% per trade" does
  not turn into "0%");
- tickers outside a small allowlist of liquid index/commodity/FX instruments →
  ``{sector} large-cap`` (or ``a single stock`` when the sector is unknown).

Replacements are parked behind sentinels while scanning so a bucket such as ``>THB 1m`` is
never re-matched by a later pass. Pure and deterministic.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel

ScrubKind = Literal["email", "thai_id", "phone", "account", "amount", "quantity", "percent", "ticker"]
PII_KINDS: frozenset[str] = frozenset({"email", "thai_id", "phone", "account"})


class ScrubHit(BaseModel):
    kind: ScrubKind
    replacement: str


def has_pii(hits: list[ScrubHit]) -> bool:
    return any(h.kind in PII_KINDS for h in hits)


# -- patterns ------------------------------------------------------------------------------------

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_MULT = r"million|billion|ล้าน|แสน|หมื่น|พัน|mn|bn|k|K|m|M"
_MULTIPLIERS: dict[str, Decimal] = {
    "k": Decimal(1_000),
    "m": Decimal(1_000_000),
    "mn": Decimal(1_000_000),
    "million": Decimal(1_000_000),
    "bn": Decimal(1_000_000_000),
    "billion": Decimal(1_000_000_000),
    "ล้าน": Decimal(1_000_000),
    "แสน": Decimal(100_000),
    "หมื่น": Decimal(10_000),
    "พัน": Decimal(1_000),
}
_CURRENCY: dict[str, str] = {
    "฿": "THB",
    "thb": "THB",
    "บาท": "THB",
    "baht": "THB",
    "$": "USD",
    "us$": "USD",
    "usd": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "ดอลลาร์": "USD",
}
_BUCKETS: dict[str, tuple[Decimal, Decimal, tuple[str, str, str]]] = {
    "THB": (Decimal(100_000), Decimal(1_000_000), ("<THB 100k", "THB 100k-1m", ">THB 1m")),
    "USD": (Decimal(3_000), Decimal(30_000), ("<USD 3k", "USD 3k-30k", ">USD 30k")),
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
THAI_ID_RE = re.compile(r"(?<!\d)(?:\d{13}|\d[- ]\d{4}[- ]\d{5}[- ]\d{2}[- ]\d)(?!\d)")
PHONE_RE = re.compile(
    r"(?<![\d+])(?:\+66[ -]?\d{1,2}[ -]?\d{3}[ -]?\d{4}"  # +66 81 234 5678
    r"|0[689]\d[ -]?\d{3}[ -]?\d{4}"  # mobile 081-234-5678
    r"|0[2-7][ -]?\d{3}[ -]?\d{4})(?!\d)"  # landline 02-123-4567
)
ACCOUNT_RE = re.compile(r"(?<!\d)(?:\d{3}-\d{1,2}-\d{4,6}-\d{1,2}|\d{10,12})(?!\d)")
AMOUNT_PREFIX_RE = re.compile(
    rf"(?P<cur>฿|US\$|\$|THB|USD)\s?(?P<num>{_NUM})(?:\s?(?P<mult>{_MULT}))?(?![A-Za-z])", re.IGNORECASE
)
AMOUNT_SUFFIX_RE = re.compile(
    rf"(?P<num>{_NUM})\s?(?P<mult>{_MULT})?\s?(?P<cur>บาท|THB|USD|baht|dollars?|ดอลลาร์)", re.IGNORECASE
)
QUANTITY_RE = re.compile(rf"(?P<num>{_NUM})\s?(?:หุ้น|หน่วย|(?i:shares?|units?))")
PERCENT_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s?%")
TICKER_RE = re.compile(r"(?<![A-Za-z0-9.^=])(?P<t>[A-Z]{2,6})(?P<suf>\.BK|-R|-F)?(?![A-Za-z0-9])")

# Liquid, widely traded instruments a generic lesson may name (index ETFs, commodities, FX majors).
ALLOWED_TICKERS: frozenset[str] = frozenset(
    {
        "SPY", "QQQ", "VOO", "VTI", "IVV", "DIA", "IWM", "EFA", "EEM", "AGG", "BND", "GLD", "IAU", "SLV",
        "TLT", "IEF", "SHY", "XLK", "XLF", "XLE", "GC", "SI", "CL", "BTC", "ETH", "XAU", "XAG", "XAUUSD",
        "EURUSD", "USDJPY", "GBPUSD", "USDTHB",
    }
)  # fmt: skip
# All-caps words that are not tickers (finance acronyms, currencies, markets, common words).
_NOT_TICKER_WORDS = """
ID     US USA UK EU TH USD THB EUR JPY GBP CNY HKD SGD SET MAI NYSE ETF ETFS REIT REITS IPO CEO CFO
    GDP CPI PPI PMI FOMC FED ECB BOT BOJ NFP VIX DXY ATR RSI MACD SMA EMA BB ADX OBV VWAP EPS PE
    PEG PB PBV ROE ROA ROI DCF WACC FCF EBIT EBITDA NAV AUM YTD QOQ YOY MOM DCA RMF SSF LTF TAX AI
    ML LLM API OK NO YES NOT AND OR THE FOR BUY SELL HOLD LONG SHORT TP SL RR BOS FVG OB EQH EQL
    SMC CHOCH HTF ATH ATL TA FA ESG FX CFD OTC SEC IMF OPEC GMT UTC BKK NA VAR CVAR MDD HHI PNL KPI
    REVIEW REJECT RETIRE ACTIVE SIGNAL NOTE IF THEN ELSE DO DONT NEVER ALWAYS ONLY ALL ANY NEW OLD
    LOW HIGH MAX MIN AVG TOP KEY RISK CASH BOND BONDS GOLD OIL FOMO YOLO MBA CFA CFP
"""
NOT_TICKERS: frozenset[str] = frozenset(_NOT_TICKER_WORDS.split())
_SECTORS: dict[str, tuple[str, ...]] = {
    "Thai bank": ("KBANK", "SCB", "BBL", "KTB", "TTB", "TISCO", "KKP"),
    "Thai energy": ("PTT", "PTTEP", "IRPC", "BCP", "GULF", "GPSC", "RATCH", "EGCO", "BANPU", "OR", "PTTGC"),
    "Thai retail": ("CPALL", "CRC", "HMPRO", "GLOBAL", "BJC", "CPAXT"),
    "Thai telecom": ("ADVANC", "TRUE", "INTUCH"),
    "Thai transport": ("AOT", "BEM", "BTS"),
    "Thai property": ("LH", "AP", "SPALI", "CPN", "WHA"),
    "Thai healthcare": ("BDMS", "BH", "BCH"),
    "Thai food": ("CPF", "TU", "OSP"),
    "US tech": ("AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA", "AMD", "INTC", "AVGO",
                "ORCL", "CRM", "NFLX", "ADBE", "TSM"),
    "US bank": ("JPM", "BAC", "WFC", "GS", "MS", "CITI"),
    "US energy": ("XOM", "CVX", "COP"),
    "US healthcare": ("JNJ", "PFE", "UNH", "LLY", "MRK"),
    "US consumer": ("KO", "PEP", "WMT", "COST", "MCD", "NKE", "SBUX"),
}  # fmt: skip
SECTOR_OF: dict[str, str] = {t: sector for sector, tickers in _SECTORS.items() for t in tickers}

_SENTINEL_BASE = 0xE000  # private-use code points: never a digit or a letter the passes look at
_SENTINEL_RE = re.compile("\x00([-])\x00")


# -- replacements --------------------------------------------------------------------------------


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:  # pragma: no cover - the regex only passes digits
        return None


def _bucket(match: re.Match[str]) -> str | None:
    number = _decimal(match.group("num"))
    if number is None:  # pragma: no cover
        return None
    mult = match.group("mult")
    if mult:
        number *= _MULTIPLIERS.get(mult, _MULTIPLIERS.get(mult.lower(), Decimal(1)))
    currency = _CURRENCY.get(match.group("cur").lower(), "THB")
    low, high, labels = _BUCKETS[currency]
    if number < low:
        return labels[0]
    if number < high:
        return labels[1]
    return labels[2]


def _percent(match: re.Match[str]) -> str | None:
    number = _decimal(match.group("num"))
    if number is None:  # pragma: no cover
        return None
    if Decimal(0) < number < Decimal("2.5"):
        return "<5%"
    rounded = (number / 5).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 5
    return f"{int(rounded)}%"


def _ticker(match: re.Match[str]) -> str | None:
    ticker = match.group("t")
    if ticker in NOT_TICKERS or ticker in ALLOWED_TICKERS:
        return None
    sector = SECTOR_OF.get(ticker)
    return f"{sector} large-cap" if sector else "a single stock"


_Replace = Callable[[re.Match[str]], str | None]
_PASSES: tuple[tuple[ScrubKind, re.Pattern[str], _Replace], ...] = (
    ("email", EMAIL_RE, lambda m: "[email]"),
    ("thai_id", THAI_ID_RE, lambda m: "[id]"),
    ("phone", PHONE_RE, lambda m: "[phone]"),
    ("account", ACCOUNT_RE, lambda m: "[account]"),
    ("amount", AMOUNT_PREFIX_RE, _bucket),
    ("amount", AMOUNT_SUFFIX_RE, _bucket),
    ("quantity", QUANTITY_RE, lambda m: "a position"),
    ("percent", PERCENT_RE, _percent),
    ("ticker", TICKER_RE, _ticker),
)


def scrub_text(text: str) -> tuple[str, list[ScrubHit]]:
    """``(clean text, hits)``; ``hits`` never contains the original value (only kind + replacement)."""
    hits: list[ScrubHit] = []
    slots: list[str] = []

    def apply(kind: ScrubKind, replace: _Replace) -> Callable[[re.Match[str]], str]:
        def sub(match: re.Match[str]) -> str:
            replacement = replace(match)
            if replacement is None:
                return match.group(0)
            hits.append(ScrubHit(kind=kind, replacement=replacement))
            slots.append(replacement)
            return f"\x00{chr(_SENTINEL_BASE + len(slots) - 1)}\x00"

        return sub

    for kind, pattern, replace in _PASSES:
        text = pattern.sub(apply(kind, replace), text)
    clean = _SENTINEL_RE.sub(lambda m: slots[ord(m.group(1)) - _SENTINEL_BASE], text)
    return clean, hits
