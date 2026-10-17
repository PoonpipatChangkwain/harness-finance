"""(P17) The always-injected investor profile block (Letta-style core memory, research §C.2).

Pure: active items only, most important first (``importance x confidence``), cut when the
next item would push the block past ``max_tokens``. Rendered as data lines, never as
instructions; the workflow adds its own heading.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from pydantic import BaseModel, Field

from app.domain.memory.schemas import CATEGORIES, MemoryCategory, MemoryItemModel

CATEGORY_LABELS: dict[MemoryCategory, str] = {
    "investment_style": "investment style",
    "risk_tolerance": "risk tolerance",
    "risk_capacity": "risk capacity",
    "goal": "goal",
    "constraint": "constraint",
    "preference": "preference",
    "life_fact": "life fact",
    "behavioral_pattern": "behavior",
    "knowledge_level": "knowledge",
}
UNCONFIRMED_LABEL = "(ยังไม่ยืนยัน)"


def estimate_tokens(text: str) -> int:
    """Rough token count without a tokenizer: ~4 ASCII chars per token, Thai/other ~1.5 chars."""
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    other = len(text) - ascii_chars
    return math.ceil(ascii_chars / 4 + other / 1.5)


class ProfileRender(BaseModel):
    text: str = ""
    token_estimate: int = 0
    items_used: list[str] = Field(default_factory=list)


def _line(item: MemoryItemModel) -> str:
    label = CATEGORY_LABELS.get(item.category, item.category)
    flag = " [stated vs revealed mismatch]" if "stated_vs_revealed" in item.flags else ""
    return f"- [{label}] {item.text.strip()}{flag}"


def _rank(item: MemoryItemModel) -> tuple[float, int, float]:
    return (
        -(item.importance * item.confidence),
        CATEGORIES.index(item.category),
        -item.valid_from.timestamp(),
    )


def render_profile(items: Iterable[MemoryItemModel], max_tokens: int = 600) -> ProfileRender:
    """Active items as ``- [category] text`` lines, best first, within ``max_tokens``."""
    lines: list[str] = []
    used: list[str] = []
    total = 0
    for item in sorted((i for i in items if i.status == "active"), key=_rank):
        line = _line(item)
        cost = estimate_tokens(line) + 1  # + newline
        if total + cost > max_tokens:
            break
        lines.append(line)
        used.append(item.id)
        total += cost
    text = "\n".join(lines)
    return ProfileRender(text=text, token_estimate=estimate_tokens(text) if text else 0, items_used=used)


def render_items(items: Iterable[MemoryItemModel]) -> str:
    """Search hits for a prompt: candidates are marked as not yet confirmed by the user."""
    out = []
    for item in items:
        mark = f" {UNCONFIRMED_LABEL}" if item.status == "candidate" else ""
        out.append(f"{_line(item)}{mark}")
    return "\n".join(out)
