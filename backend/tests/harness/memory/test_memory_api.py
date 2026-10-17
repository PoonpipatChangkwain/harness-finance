"""(P17) ``/api/memory*`` and ``/api/reasoning-bank*``: every endpoint, 404/409/422/400, and the
``X-User-Id`` header keeping two users' memories apart."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.memory.schemas import OutcomeJudgement, PrivacyVerdict
from app.domain.ports import ToolCallReq
from app.harness.events.bus import EventBus
from app.harness.executor.registry import ToolRegistry
from app.harness.llm.fake import FakeLLM
from app.harness.memory.store import NewMemory
from app.harness.memory.tools import user_memory_tool
from app.harness.runtime.services import HarnessServices
from app.harness.storage import runs_repo
from app.settings import Settings
from tests.harness.memory.helpers import USER, lesson, lessons
from tests.harness.portfolio.helpers import client

pytestmark = pytest.mark.usefixtures("db")

OTHER = {"X-User-Id": "usr_alice"}


def services(settings: Settings, llm: FakeLLM | None = None) -> HarnessServices:
    return HarnessServices(
        settings=settings, bus=EventBus(), tools=ToolRegistry([]), llm=llm if llm is not None else FakeLLM()
    )


async def test_create_list_profile_and_user_isolation(settings: Settings, install_services: Any) -> None:
    svc = install_services(services(settings))
    async with client() as c:
        r = await c.post("/api/memory", json={"category": "goal", "text": "ผู้ใช้เก็บเงินดาวน์คอนโดปี 2029"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert [ch["op"] for ch in body["changes"]] == ["ADD"]
        goal = body["changes"][0]["item"]
        assert goal["source"] == "manual" and goal["confidence"] == 0.95 and "user_id" not in goal
        r = await c.post(
            "/api/memory", json={"category": "preference", "text": "ผู้ใช้อีกคนชอบทองคำ"}, headers=OTHER
        )
        assert r.status_code == 200
        mine = (await c.get("/api/memory")).json()
        assert [i["id"] for i in mine["items"]] == [goal["id"]]
        assert mine["counts"] == {"active": 1, "candidate": 0, "superseded": 0, "retired": 0, "rejected": 0}
        theirs = (await c.get("/api/memory", headers=OTHER)).json()
        assert [i["text"] for i in theirs["items"]] == ["ผู้ใช้อีกคนชอบทองคำ"]
        assert (await c.get(f"/api/memory/{goal['id']}/history", headers=OTHER)).status_code == 404
        profile = (await c.get("/api/memory/profile")).json()
        assert profile["profile_text"] == "- [goal] ผู้ใช้เก็บเงินดาวน์คอนโดปี 2029"
        assert profile["items_used"] == [goal["id"]] and profile["token_estimate"] > 0
        assert profile["updated_at"] is not None
        assert (await c.get("/api/memory?status=candidate")).json()["items"] == []
        assert (await c.get("/api/memory?category=preference")).json()["items"] == []
        assert (await c.get("/api/memory", headers={"X-User-Id": "alice"})).status_code == 400
        assert (await c.get("/api/memory?status=bogus")).status_code == 422
        assert (await c.post("/api/memory", json={"category": "nope", "text": "abc"})).status_code == 422
        assert (await c.post("/api/memory", json={"category": "goal", "text": "x"})).status_code == 422
    assert svc.memory_engine.bank is svc.reasoning_bank


async def test_patch_confirm_reject_retire_history_delete(settings: Settings, install_services: Any) -> None:
    svc = install_services(services(settings))
    store = svc.user_memory
    item = await store.add(USER, NewMemory(category="investment_style", text="ผู้ใช้ชอบลงทุนระยะยาว", key="pace"))
    cand = await store.add(
        USER, NewMemory(category="risk_tolerance", text="ผู้ใช้อาจรับความเสี่ยงสูง", status="candidate")
    )
    cand2 = await store.add(USER, NewMemory(category="goal", text="ผู้ใช้อาจอยากเกษียณเร็ว", status="candidate"))
    async with client() as c:
        r = await c.patch(f"/api/memory/{item.id}", json={"text": "ผู้ใช้ชอบลงทุนระยะสั้น"})
        assert r.status_code == 200, r.text
        edited = r.json()
        assert edited["supersedes"] == item.id and edited["source"] == "manual" and edited["key"] == "pace"
        assert (await c.patch(f"/api/memory/{item.id}", json={"text": "again!"})).status_code == 409
        assert (await c.patch(f"/api/memory/{edited['id']}", json={})).status_code == 422
        assert (await c.patch("/api/memory/mem_missing", json={"text": "abc"})).status_code == 404
        r = await c.patch(f"/api/memory/{edited['id']}", json={"value_json": {"pace": "fast"}})
        assert r.json()["value_json"] == {"pace": "fast"} and r.json()["text"] == "ผู้ใช้ชอบลงทุนระยะสั้น"
        latest = r.json()["id"]
        history = (await c.get(f"/api/memory/{latest}/history")).json()["items"]
        assert [h["id"] for h in history] == [item.id, edited["id"], latest]
        assert [h["status"] for h in history] == ["superseded", "superseded", "active"]

        r = await c.post(f"/api/memory/{cand.id}/confirm")
        assert (
            r.status_code == 200 and r.json()["status"] == "active" and r.json()["source"] == "user_confirmed"
        )
        assert (await c.post(f"/api/memory/{cand.id}/confirm")).status_code == 409
        assert (await c.post(f"/api/memory/{cand2.id}/reject")).json()["status"] == "rejected"
        assert (await c.post(f"/api/memory/{cand2.id}/reject")).status_code == 409
        r = await c.post(f"/api/memory/{latest}/retire")
        assert r.json()["status"] == "retired" and r.json()["valid_to"] is not None
        assert (await c.post(f"/api/memory/{latest}/retire")).status_code == 409
        assert (await c.post("/api/memory/mem_missing/confirm")).status_code == 404
        assert (await c.post("/api/memory/mem_missing/retire", headers=OTHER)).status_code == 404

        counts = (await c.get("/api/memory")).json()["counts"]
        assert counts == {"active": 1, "candidate": 0, "superseded": 2, "retired": 1, "rejected": 1}

        r = await c.delete(f"/api/memory/{latest}?cascade=1", headers=OTHER)
        assert r.status_code == 404
        r = await c.delete(f"/api/memory/{latest}?cascade=1")
        assert sorted(r.json()["deleted"]) == sorted([item.id, edited["id"], latest])
        assert (await c.get(f"/api/memory/{latest}/history")).status_code == 404
        assert (await c.delete(f"/api/memory/{latest}")).status_code == 404
        assert await store.fts_ids(USER) == {i["id"] for i in (await c.get("/api/memory")).json()["items"]}
        r = await c.delete(f"/api/memory/{cand.id}")
        assert r.json() == {"deleted": [cand.id]}


async def test_post_memory_reconciles_through_the_llm(settings: Settings, install_services: Any) -> None:
    from app.domain.memory.schemas import ReconcileOp, ReconcileOps

    llm = FakeLLM()
    svc = install_services(services(settings, llm))
    old = await svc.user_memory.add(USER, NewMemory(category="preference", text="ผู้ใช้ชอบหุ้นปันผล"))
    llm.push(
        ReconcileOps(ops=[ReconcileOp(op="SUPERSEDE", target_id=old.id, text="ผู้ใช้ชอบหุ้นเติบโตมากกว่าปันผล")])
    )
    async with client() as c:
        r = await c.post("/api/memory", json={"category": "preference", "text": "ผู้ใช้ชอบหุ้นเติบโตแทนปันผล"})
    body = r.json()
    assert [ch["op"] for ch in body["changes"]] == ["SUPERSEDE"] and body["warnings"] == []
    assert body["changes"][0]["previous"]["id"] == old.id
    assert body["changes"][0]["item"]["text"] == "ผู้ใช้ชอบหุ้นเติบโตมากกว่าปันผล"
    assert llm.calls[0].purpose == "memory" and llm.calls[0].tier == "quick"


async def test_reasoning_bank_endpoints(settings: Settings, install_services: Any) -> None:
    ok = PrivacyVerdict(identifies_person_or_holdings=False, reason="generic")
    llm = FakeLLM(
        [lessons(lesson("Confirm the horizon first"), lesson("Coach before sizing", kind="coaching")), ok, ok]
    )
    svc = install_services(services(settings, llm))
    items = await svc.reasoning_bank.distill(
        run_summary="s",
        outcome=OutcomeJudgement(success=True, score=0.8),
        tags={"workflow": "portfolio"},
        source_run_id="run_P",
        user_id=USER,
    )
    await svc.reasoning_bank.record_use([items[0].id], "run_Q", USER)
    await svc.reasoning_bank.settle("run_Q", True)
    async with client() as c:
        listed = (await c.get("/api/reasoning-bank")).json()
        assert len(listed["items"]) == 2 and listed["counts"] == {
            "total": 2,
            "candidate": 0,
            "active": 2,
            "retired": 0,
        }
        assert "user_id" not in listed["items"][0]
        assert [i["kind"] for i in (await c.get("/api/reasoning-bank?kind=coaching")).json()["items"]] == [
            "coaching"
        ]
        assert (await c.get("/api/reasoning-bank?workflow=trade")).json()["items"] == []
        assert len((await c.get("/api/reasoning-bank?q=Confirm")).json()["items"]) == 1
        assert (await c.get("/api/reasoning-bank?workflow=bogus")).status_code == 422
        detail = (await c.get(f"/api/reasoning-bank/{items[0].id}")).json()
        assert detail["item"]["use_count"] == 1 and detail["item"]["success_rate"] == pytest.approx(2 / 3)
        assert [(u["run_id"], u["outcome"]) for u in detail["uses"]] == [("run_Q", "success")]
        assert (await c.get("/api/reasoning-bank/rb_missing")).status_code == 404
        stats = (await c.get("/api/reasoning-bank/stats")).json()
        assert stats["total"] == 2 and stats["by_workflow"] == {"portfolio": 2}
        assert stats["by_kind"] == {"strategy": 1, "coaching": 1}
        r = await c.post(f"/api/reasoning-bank/{items[1].id}/retire")
        assert r.json()["status"] == "retired"
        assert (await c.post("/api/reasoning-bank/rb_missing/retire")).status_code == 404
        assert (await c.get("/api/reasoning-bank?status=retired")).json()["counts"]["retired"] == 1


async def test_user_memory_search_tool_scoped_to_run_user(
    settings: Settings, install_services: Any, tmp_path: Any
) -> None:
    from tests.harness.conftest import make_ctx

    svc = install_services(services(settings))
    store = svc.user_memory
    await store.add(USER, NewMemory(category="constraint", text="ผู้ใช้ไม่ขาย KBANK เด็ดขาด", importance=9))
    await store.add(USER, NewMemory(category="preference", text="ผู้ใช้ชอบคำตอบสั้น", importance=3))
    await store.add(USER, NewMemory(category="goal", text="ผู้ใช้อาจเกษียณเร็ว", status="candidate"))
    await store.add("usr_alice", NewMemory(category="constraint", text="อลิซไม่ขาย KBANK"))
    from app.harness.executor.executor import ToolExecutor

    registry = ToolRegistry([user_memory_tool(store)])
    await runs_repo.create_run("run_MEMTOOL", "research", {"text": "q"})
    ctx = make_ctx(tmp_path, run_id="run_MEMTOOL")
    executor = ToolExecutor(registry, ctx)
    [res] = await executor.run_calls(
        [ToolCallReq(id="c1", name="user_memory_search", args={"query": "KBANK"})]
    )
    assert res.ok, res
    assert [d["text"] for d in res.data] == ["ผู้ใช้ไม่ขาย KBANK เด็ดขาด"]
    [res] = await executor.run_calls([ToolCallReq(id="c2", name="user_memory_search", args={"limit": 5})])
    assert [d["text"] for d in res.data] == ["ผู้ใช้ไม่ขาย KBANK เด็ดขาด", "ผู้ใช้ชอบคำตอบสั้น"]  # active only
    await runs_repo.create_run("run_ALICE", "research", {"text": "q", "user_id": "usr_alice"})
    executor = ToolExecutor(registry, make_ctx(tmp_path / "alice", run_id="run_ALICE"))
    [res] = await executor.run_calls(
        [ToolCallReq(id="c3", name="user_memory_search", args={"query": "KBANK"})]
    )
    assert [d["text"] for d in res.data] == ["อลิซไม่ขาย KBANK"]
