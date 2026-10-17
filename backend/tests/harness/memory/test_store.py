"""(P17) ``UserMemoryStore``: CRUD scoped by user, Thai FTS, supersede chain, forget (+FTS) and confirm."""

from __future__ import annotations

import pytest

from app.harness.memory.store import UserMemoryStore, fts_query
from tests.harness.memory.helpers import USER, row

pytestmark = pytest.mark.usefixtures("db")


async def test_add_get_list_counts_sorted() -> None:
    store = UserMemoryStore()
    a = await store.add(USER, row("ผู้ใช้ชอบ ETF", category="preference", importance=4))
    b = await store.add(USER, row("ผู้ใช้เก็บเงินดาวน์คอนโด", category="goal", importance=9))
    c = await store.add(USER, row("ผู้ใช้ลงทุนระยะยาว", importance=8, status="candidate"))
    assert a.id.startswith("mem_") and len(a.id) == 30
    assert (await store.get(USER, a.id)) == a
    assert await store.get("usr_other", a.id) is None
    listed = await store.list_items(USER)
    assert [i.id for i in listed] == [c.id, b.id, a.id]  # investment_style → goal → preference
    assert [i.id for i in await store.list_items(USER, statuses=["active"])] == [b.id, a.id]
    counts = await store.counts(USER)
    assert counts == {"candidate": 1, "active": 2, "superseded": 0, "retired": 0, "rejected": 0}
    assert (await store.counts(USER, category="goal"))["active"] == 1


async def test_thai_fts_substring_and_user_scope() -> None:
    store = UserMemoryStore()
    mine = await store.add(USER, row("ผู้ใช้ชอบลงทุนระยะยาวแบบไม่เร่งรีบ"))
    await store.add(USER, row("User prefers dividend stocks", category="preference"))
    await store.add("usr_other", row("ผู้ใช้ชอบลงทุนระยะยาวเหมือนกัน"))
    hits = await store.search_fts(USER, "ระยะยาว")
    assert [i.id for i, _ in hits] == [mine.id]
    assert hits[0][1] > 0
    # a Thai sentence without spaces still finds it (overlapping windows)
    assert [i.id for i, _ in await store.search_fts(USER, "ผมชอบลงทุนระยะยาว")] == [mine.id]
    assert [i.text for i, _ in await store.search_fts(USER, "DIVIDEND")] == ["User prefers dividend stocks"]
    assert await store.search_fts(USER, "ab") == []  # < 3 characters: no FTS term
    assert await store.search_fts(USER, "ระยะยาว", category="goal") == []


def test_fts_query_quotes_terms() -> None:
    assert fts_query('NVDA "AI" x KBANK NVDA') == '"NVDA" OR "KBANK"'  # < 3 chars dropped, deduped
    assert fts_query("  ") is None
    q = fts_query("ผมชอบลงทุนระยะยาว")
    assert q is not None and '"ระยะ' in q and " OR " in q


async def test_supersede_chain_history_and_fts_consistency() -> None:
    store = UserMemoryStore()
    old = await store.add(USER, row("ผู้ใช้ชอบลงทุนเรื่อย ๆ ระยะยาว", key="pace"))
    done = await store.supersede(USER, old.id, row("ผู้ใช้ตอนนี้ชอบลงทุนเร็ว ๆ ระยะสั้น", key="pace"))
    assert done is not None
    new, previous = done
    assert new.supersedes == old.id and new.status == "active"
    assert (
        previous.status == "superseded" and previous.superseded_by == new.id and previous.valid_to is not None
    )
    newest = await store.supersede(USER, new.id, row("ผู้ใช้ชอบเทรดรายวัน", key="pace"))
    assert newest is not None
    history = await store.history(USER, new.id)
    assert [i.id for i in history] == [old.id, new.id, newest[0].id]
    assert await store.fts_ids(USER) == {i.id for i in await store.list_items(USER)}
    # only the current one is found by search (superseded rows stay indexed but filtered)
    assert [i.id for i, _ in await store.search_fts(USER, "ระยะยาว")] == []
    assert await store.supersede(USER, "mem_missing", row("x x x")) is None


async def test_forget_cascade_removes_chain_and_fts() -> None:
    store = UserMemoryStore()
    keep = await store.add(USER, row("ผู้ใช้ชอบ ETF ดัชนี", category="preference"))
    old = await store.add(USER, row("ผู้ใช้ชอบลงทุนระยะยาว"))
    done = await store.supersede(USER, old.id, row("ผู้ใช้ชอบลงทุนระยะสั้น"))
    assert done is not None
    assert await store.forget("usr_other", old.id, cascade=True) == []
    deleted = await store.forget(USER, done[0].id, cascade=True)
    assert set(deleted) == {old.id, done[0].id}
    assert [i.id for i in await store.list_items(USER)] == [keep.id]
    assert await store.fts_ids(USER) == {keep.id}
    assert await store.history(USER, old.id) == []
    assert await store.forget(USER, old.id) == []


async def test_forget_single_row_unlinks_neighbours() -> None:
    store = UserMemoryStore()
    old = await store.add(USER, row("ผู้ใช้ชอบลงทุนระยะยาว"))
    done = await store.supersede(USER, old.id, row("ผู้ใช้ชอบลงทุนระยะสั้น"))
    assert done is not None
    assert await store.forget(USER, old.id) == [old.id]
    current = await store.get(USER, done[0].id)
    assert current is not None and current.supersedes is None
    assert await store.fts_ids(USER) == {done[0].id}


async def test_confirm_candidate_supersedes_the_row_it_replaces() -> None:
    store = UserMemoryStore()
    active = await store.add(
        USER, row("ผู้ใช้รับความเสี่ยงได้ 7/10", category="risk_tolerance", key="risk_tolerance.score")
    )
    cand = await store.link_candidate(
        USER,
        row("ผู้ใช้รับความเสี่ยงได้ 4/10", category="risk_tolerance", key="risk_tolerance.score"),
        active.id,
    )
    assert cand.status == "candidate" and cand.supersedes == active.id
    assert (await store.get(USER, active.id)).status == "active"  # type: ignore[union-attr]
    done = await store.confirm(USER, cand.id)
    assert done is not None
    confirmed, superseded = done
    assert (
        confirmed.status == "active" and confirmed.source == "user_confirmed" and confirmed.confidence == 0.95
    )
    assert [s.id for s in superseded] == [active.id]
    old = await store.get(USER, active.id)
    assert old is not None and old.status == "superseded" and old.superseded_by == cand.id


async def test_touch_and_set_status() -> None:
    store = UserMemoryStore()
    item = await store.add(USER, row("ผู้ใช้ชอบหุ้นปันผล", category="preference"))
    await store.touch(USER, [item.id, item.id])
    touched = await store.get(USER, item.id)
    assert touched is not None and touched.access_count == 1 and touched.last_accessed_at is not None
    retired = await store.set_status(USER, item.id, "retired")
    assert retired is not None and retired.status == "retired" and retired.valid_to is not None
    assert await store.set_status(USER, "mem_nope", "retired") is None
