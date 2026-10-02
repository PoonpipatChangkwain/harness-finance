"""Hallucination check: numbers in a final answer that no provenance record backs (F3).

``find_unverified_numbers(markdown, records)`` extracts every number
(``1,234.5``, ``12.3%``, ``$45``, ``฿45``, ``1.2B``, ``3.4 ล้าน``),
normalizes magnitude suffixes, and looks for a record value (or a numeric
param) within tolerance: 0.5% relative, or the rounding implied by the
digits written ("34.5" covers 34.45 to 34.55). Percentages also match their
ratio form (12.3% ↔ 0.123), and the sign is ignored ("fell 5%" ↔ -0.05).

Skipped as not-a-claim: years (1900 to 2100, and Buddhist-era 2400 to 2700),
dates, times, numbered-list markers, bare counts ≤ 10 with no unit, and
period labels ("50 วัน", "3-month", "ไตรมาส 2", "FY 26").

``cite_numbers`` (P07) inserts ``[^prov_…]`` after every number a record backs
and that has no marker yet, so citations do not depend on the model writing
them; the most specific record (fewest numbers) wins.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator
from decimal import Decimal
from typing import Any

from app.domain.models.provenance import ProvenanceRecord

_MULTIPLIERS: dict[str, float] = {
    "ล้านล้าน": 1e12,
    "พันล้าน": 1e9,
    "ล้าน": 1e6,
    "แสน": 1e5,
    "หมื่น": 1e4,
    "พัน": 1e3,
    "trillion": 1e12,
    "billion": 1e9,
    "million": 1e6,
    "thousand": 1e3,
    "bn": 1e9,
    "mn": 1e6,
    "t": 1e12,
    "b": 1e9,
    "m": 1e6,
    "k": 1e3,
}

_NUMBER_RE = re.compile(
    r"""
    (?P<cur>[$฿€£¥])?\s?
    (?<![A-Za-z0-9_.])
    (?P<num>[-+\N{MINUS SIGN}]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)
    (?:\s?(?P<suffix>%|ล้านล้าน|พันล้าน|ล้าน|แสน|หมื่น|พัน
        |(?i:trillion|billion|million|thousand|bn|mn)\b|[kKmMbBtT](?![A-Za-z])))?
    """,
    re.VERBOSE,
)

_THAI_MONTHS = (
    "มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม"
    r"|ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\."
)
_EN_MONTHS = (
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december"
    r"|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\b\.?"
)

_MASKS = [
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?Z?)?\b"),  # ISO dates
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),  # 24/09/2026
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),  # times
    re.compile(  # "24 กันยายน 2569", "24 ก.ย.", "Sep 24, 2026", "24 Sep"
        rf"(?:\b\d{{1,2}}\s?(?:{_THAI_MONTHS}|{_EN_MONTHS})(?:\s?\d{{4}})?"
        rf"|\b(?:{_EN_MONTHS})\s\d{{1,2}}(?:,?\s\d{{4}})?)",
        re.IGNORECASE,
    ),
    re.compile(r"(?m)^\s*\d+[.)]\s"),  # numbered list markers
    re.compile(r"\[\^?prov_[A-Za-z0-9]+\]|\[\d+\]"),  # citation markers
]


_PERIOD_AFTER = re.compile(
    r"\A[\s-]?(?:วัน|สัปดาห์|เดือน|ปี|ไตรมาส|days?|weeks?|months?|years?|quarters?|yrs?|mos?|d|w|m|y)(?![A-Za-z])",
    re.IGNORECASE,
)
_PERIOD_BEFORE = re.compile(
    r"(?:(?:ไตรมาส|quarter|FY|Q|H|ปี|year|SMA|EMA|RSI|MA|ATR)\s?[\s(-]?|(?:SMA|EMA|MA)\s?\d+\s?/\s?)\Z",
    re.IGNORECASE,
)
# a marker right after the number, or after a short unit word ("341.07 ดอลลาร์สหรัฐ [^prov_…]")
_CITED_AFTER = re.compile(r"\A[^\d\[\]\n|.,;:()]{0,24}\[(?:\^prov_|\d)")


def _mask(text: str) -> str:
    for pattern in _MASKS:
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _numbers_in(value: Any, depth: int = 0) -> Iterator[float]:
    if depth > 6:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int | float | Decimal):
        f = float(value)
        if math.isfinite(f):
            yield f
    elif isinstance(value, str):
        try:
            yield float(value.replace(",", ""))
        except ValueError:
            return
    elif isinstance(value, dict):
        for v in value.values():
            yield from _numbers_in(v, depth + 1)
    elif isinstance(value, list | tuple):
        for v in value[:5000]:
            yield from _numbers_in(v, depth + 1)


def record_values(records: Iterable[ProvenanceRecord]) -> list[float]:
    values: list[float] = []
    for rec in records:
        values.extend(_numbers_in(rec.value))
        values.extend(_numbers_in(rec.params))
    return values


def _close(x: float, values: list[float], abs_tol: float, *, relative: bool = True) -> bool:
    ax = abs(x)
    for v in values:
        av = abs(v)
        if abs(ax - av) <= max(0.005 * av if relative else 0.0, abs_tol) + 1e-12:
            return True
    return False


def _is_skippable(num: str, value: float, cur: str | None, suffix: str | None) -> bool:
    if cur or suffix:
        return False
    if "." in num or "," in num:
        return False
    if 1900 <= value <= 2100 or 2400 <= value <= 2700:
        return True  # years (incl. Thai Buddhist era)
    return 0 <= value <= 10  # bare small counts ("3 analysts")


Candidate = tuple[float, float, bool]  # (value, absolute tolerance, implied scale)


def _candidates(match: re.Match[str], text: str) -> list[Candidate] | None:
    """Values a number token could mean, or None when it is not a numeric claim."""
    num = match.group("num").replace("\N{MINUS SIGN}", "-")
    cur = match.group("cur")
    suffix = match.group("suffix")
    base = float(num.replace(",", ""))
    if _is_skippable(num, abs(base), cur, suffix):
        return None
    if not cur and suffix in ("m", "M") and num.isdigit() and 1 <= base <= 12:
        return None  # "1M", "3M": a period label, not 3 million
    if not cur and not suffix and "." not in num and "," not in num:
        if _PERIOD_AFTER.match(text[match.end() : match.end() + 12]):
            return None
        if _PERIOD_BEFORE.search(text[max(0, match.start("num") - 8) : match.start("num")]):
            return None
    decimals = len(num.split(".")[1]) if "." in num else 0
    rounding = 0.5 * 10 ** (-decimals)
    if suffix == "%":
        return [(base, rounding, False), (base / 100, rounding / 100, False)]
    mult = _MULTIPLIERS.get(suffix.lower(), 1.0) if suffix else 1.0
    out = [(base * mult, rounding * mult, False)]
    if not suffix and (decimals >= 2 or "," in num):
        # unit stated elsewhere (table header "USD bn", "ล้านบาท"): allow an implied scale
        out += [(base * k, rounding * k, True) for k in (1e3, 1e6, 1e9, 1e12)]
    return out


def find_unverified_numbers(markdown: str, records: Iterable[ProvenanceRecord]) -> list[str]:
    values = record_values(records)
    unverified: list[str] = []
    for match in _NUMBER_RE.finditer(_mask(markdown)):
        candidates = _candidates(match, markdown)
        if candidates is None:
            continue
        if not any(_close(x, values, tol) for x, tol, _ in candidates):
            raw = match.group(0).strip()
            if raw not in unverified:
                unverified.append(raw)
    return unverified


def cite_numbers(markdown: str, records: Iterable[ProvenanceRecord]) -> str:
    """Append ``[^prov_id]`` to each uncited number that some record backs."""
    index = []
    for rec in records:
        nums = [*_numbers_in(rec.value), *_numbers_in(rec.params)]
        if nums:
            index.append((len(nums), rec.id, nums))
    index.sort(key=lambda item: item[0])
    out: list[str] = []
    last = 0
    for match in _NUMBER_RE.finditer(_mask(markdown)):
        end = match.end()
        if _CITED_AFTER.match(markdown[end : end + 12]):
            continue
        candidates = _candidates(match, markdown)
        if candidates is None:
            continue
        # an implied-scale match must be within the written rounding: citing the wrong record is
        # worse than citing none ("10,999 หยวน" must not land on a 10.99bn statement line)
        pid = next(
            (
                rid
                for _, rid, nums in index
                if any(_close(x, nums, tol, relative=not implied) for x, tol, implied in candidates)
            ),
            None,
        )
        if pid is None:
            continue
        stripped_end = end - (len(match.group(0)) - len(match.group(0).rstrip()))
        out.append(markdown[last:stripped_end] + f"[^{pid}]")
        last = stripped_end
    out.append(markdown[last:])
    return "".join(out)
