"""(P17) ``render_profile``: active only, most important first, cut at the token budget."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.memory.profile import UNCONFIRMED_LABEL, estimate_tokens, render_items, render_profile
from app.domain.memory.schemas import MemoryItemModel

NOW = datetime(2026, 10, 17, tzinfo=UTC)


def item(i: int, **kw: Any) -> MemoryItemModel:
    base: dict[str, Any] = {
        "id": f"mem_{i}",
        "user_id": "usr_default",
        "category": "preference",
        "text": f"User fact number {i}",
        "confidence": 0.9,
        "importance": 5,
        "source": "user_stated",
        "status": "active",
        "valid_from": NOW - timedelta(days=i),
        "created_at": NOW - timedelta(days=i),
        "updated_at": NOW - timedelta(days=i),
    }
    return MemoryItemModel(**(base | kw))


def test_orders_by_importance_times_confidence_and_skips_inactive() -> None:
    items = [
        item(1, importance=3),
        item(2, importance=9, category="goal", text="ผู้ใช้เก็บเงินดาวน์คอนโดปี 2029"),
        item(3, importance=9, confidence=0.5),
        item(4, importance=10, status="superseded"),
        item(5, importance=10, status="candidate"),
    ]
    out = render_profile(items)
    assert out.items_used == ["mem_2", "mem_3", "mem_1"]
    assert out.text.splitlines()[0] == "- [goal] ผู้ใช้เก็บเงินดาวน์คอนโดปี 2029"
    assert out.token_estimate == estimate_tokens(out.text)


def test_cuts_at_max_tokens() -> None:
    items = [item(i, importance=10 - (i % 10), text="User " + "word " * 40) for i in range(40)]
    out = render_profile(items, max_tokens=600)
    assert 0 < len(out.items_used) < 40
    assert out.token_estimate <= 600
    # the most important ones made it in
    kept = {i.id for i in items if i.importance >= 9}
    assert kept <= set(out.items_used) or len(out.items_used) < len(kept)
    assert render_profile([], max_tokens=600).text == ""


def test_flags_and_unconfirmed_labels() -> None:
    flagged = item(1, category="behavioral_pattern", source_ref={"flags": ["stated_vs_revealed"]})
    assert "stated vs revealed" in render_profile([flagged]).text
    text = render_items([item(2, status="candidate"), item(3)])
    lines = text.splitlines()
    assert lines[0].endswith(UNCONFIRMED_LABEL)
    assert not lines[1].endswith(UNCONFIRMED_LABEL)


def test_estimate_tokens_counts_thai_heavier() -> None:
    assert estimate_tokens("abcd" * 10) == 10
    assert estimate_tokens("ก" * 15) == 10
