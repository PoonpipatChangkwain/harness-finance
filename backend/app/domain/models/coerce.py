"""Coerce LLM-written numeric fields before Pydantic validation (T5).

Ported from ``../TradingAgents/tradingagents/agents/schemas.py``
(``_coerce_optional_float``). Models write placeholders ("N/A"), formatted
prices ("$1,234.50"), percentages where a price was asked for ("15%"), and
ranges or hedges ("150-160", "around 150"). One bad field should null out,
not discard the whole decision, so every non-number collapses to ``None``.

Use as ``field_validator(..., mode="before")`` in domain schemas::

    class TraderProposal(BaseModel):
        stop_loss: float | None = None
        _c = field_validator("stop_loss", mode="before")(coerce_optional_float)
"""

from __future__ import annotations

import re
from typing import Any

_NULLISH = frozenset(
    {"", "none", "null", "n/a", "na", "nan", "-", "--", "—", "tbd", "unknown", "not provided"}
)
_CURRENCY_CHARS = "$€£¥฿"
# A leading sign is kept; a dash anywhere else means a range ("150-160").
_NUMBER_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _clean_number_text(text: str) -> str:
    cleaned = text.replace(",", "").replace("_", "").strip()
    cleaned = cleaned.strip(_CURRENCY_CHARS).strip()
    for suffix in ("usd", "thb", "baht", "บาท"):
        if cleaned.lower().endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    return cleaned.strip(_CURRENCY_CHARS).strip()


def coerce_optional_float(value: Any) -> Any:
    """Price-like field: "$1,234.50" → 1234.5; "N/A" / "15%" / "150-160" / "around 150" → None.

    Non-strings pass through untouched so Pydantic still validates them.
    """
    if isinstance(value, bool):
        return None
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.lower() in _NULLISH or text.endswith("%"):
        return None
    cleaned = _clean_number_text(text)
    if not _NUMBER_RE.match(cleaned):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def coerce_percent_field(value: Any) -> Any:
    """Proportion field (0..1): "15%" → 0.15, "0.15" → 0.15, 15 → 0.15, "N/A" → None.

    A bare number greater than 1 is read as a percentage (the model wrote
    "15" meaning 15%). Weights are stored as 0..1 everywhere (CLAUDE.md).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value / 100.0 if value > 1 else float(value)
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.lower() in _NULLISH:
        return None
    is_pct = text.endswith("%")
    cleaned = _clean_number_text(text.rstrip("%").strip())
    if not _NUMBER_RE.match(cleaned):
        return None
    number = float(cleaned)
    if is_pct or number > 1:
        return number / 100.0
    return number
