"""Event streams for the frontend reducer tests (P08 §8.6), recorded from the real harness.

Each scenario runs the research workflow with FakeLLM + fake tools and checks
the stream has what the frontend fixture is meant to cover. With
``RECORD_FE_FIXTURES=1`` the streams are also written (as the SSE envelope)
to ``frontend/tests/fixtures/research-run-<name>.jsonl``:

    RECORD_FE_FIXTURES=1 uv run pytest tests/harness/research/test_frontend_fixtures.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.domain.ports import LLMMessage, LLMResponse, ToolCallReq
from app.harness.events.schema import EventEnvelope
from app.harness.llm.fake import FakeTurn
from app.harness.storage import events_repo, sessions_repo
from app.settings import Settings
from tests.harness.research.helpers import CitingFakeLLM, plan, research_services

pytestmark = pytest.mark.usefixtures("db")

FIXTURES = Path(__file__).resolve().parents[4] / "frontend" / "tests" / "fixtures"


def call(name: str, id_: str, **args: Any) -> ToolCallReq:
    return ToolCallReq(id=id_, name=name, args=args)


async def envelopes(run_id: str, session_id: str | None) -> list[dict[str, Any]]:
    rows = await events_repo.events_after(run_id, 0)
    return [
        EventEnvelope(
            seq=r.seq,
            run_id=r.run_id,
            session_id=session_id,
            ts=r.ts,
            type=r.type,
            node=r.node,
            call_id=r.call_id,
            parent_call_id=r.parent_call_id,
            payload=r.payload,
        ).model_dump(mode="json")
        for r in rows
    ]


def save(name: str, events: list[dict[str, Any]]) -> None:
    if os.environ.get("RECORD_FE_FIXTURES") != "1":
        return
    FIXTURES.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e, ensure_ascii=False) for e in events]
    (FIXTURES / f"research-run-{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def of_type(events: list[dict[str, Any]], type_: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == type_]


async def test_concurrent_tools_with_subcall_and_progress(settings: Settings) -> None:
    llm = CitingFakeLLM(
        [
            plan(skill=None, title="PTT concurrent"),
            FakeTurn(
                tool_calls=[
                    call("skill_tool", "call_A1", symbol="PTT"),
                    call("progress", "call_A2", label="p"),
                    call("get_prices", "call_A3", symbol="PTT"),
                ]
            ),
            FakeTurn(tool_calls=[call("boom", "call_A4", label="x")]),
            FakeTurn(text="Brief: PTT close 34.5"),
            FakeTurn(text="PTT ปิดที่ 34.5 บาท {cite} และ 99.9 ไม่มีที่มา"),
        ]
    )
    services = research_services(settings, [], llm=llm)
    session = await sessions_repo.create_session("research")
    run_id = await services.runs.start(
        "research", {"text": "PTT ราคา", "skill": None, "history": []}, session_id=session.id
    )
    await services.runs.wait(run_id)
    events = await envelopes(run_id, session.id)

    started = of_type(events, "tool.started")
    roots = [e for e in started if e["parent_call_id"] is None]
    assert len({e["payload"]["batch_id"] for e in roots[:3]}) == 1
    assert any(e["parent_call_id"] == "call_A1" for e in started)
    assert of_type(events, "tool.progress")
    assert any(e["payload"]["status"] == "error" for e in of_type(events, "tool.finished"))
    answer = of_type(events, "answer.final")[0]["payload"]
    assert answer["citations"] and "99.9" in answer["unverified_numbers"]
    save("concurrent-subcall", events)


async def test_compaction_and_guard_limit(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path,
        app_env="dev",
        context_memory_flush=False,
    )
    llm = CitingFakeLLM(
        [
            plan(skill=None, title="PTT compaction"),
            # reported usage above the 95k threshold of an unknown model → full compaction next call
            FakeTurn(
                tool_calls=[call("big", f"call_B{i}", label=f"b{i}") for i in range(3)], input_tokens=120_000
            ),
            "## Summary\n- big results b0..b2 [prov none]",  # compaction summary (purpose compact)
            FakeTurn(tool_calls=[call("get_prices", "call_B7", symbol="PTT")]),
            FakeTurn(tool_calls=[call("get_prices", "call_B8", symbol="PTT")]),  # similar → guard
            FakeTurn(text="Brief: PTT close 34.5"),
            FakeTurn(text="PTT ปิดที่ 34.5 บาท {cite}"),
        ]
    )
    services = research_services(settings, [], llm=llm)
    session = await sessions_repo.create_session("research")
    run_id = await services.runs.start(
        "research", {"text": "PTT ราคา", "skill": None, "history": []}, session_id=session.id
    )
    await services.runs.wait(run_id)
    events = await envelopes(run_id, session.id)

    phases = [e["payload"]["phase"] for e in of_type(events, "context.compaction")]
    assert "start" in phases and ("end" in phases or "fallback_clear" in phases), phases
    assert of_type(events, "guard.limit")
    save("compaction-guard", events)


class SlowSecondAgentLLM(CitingFakeLLM):
    async def complete(
        self, messages: list[LLMMessage], *, tier: Any, tools: Any = None, purpose: str
    ) -> LLMResponse:
        if purpose == "agent" and sum(c.purpose == "agent" for c in self.calls) >= 1:
            await asyncio.sleep(30)
        return await super().complete(messages, tier=tier, tools=tools, purpose=purpose)


async def test_cancel_mid_run(settings: Settings) -> None:
    llm = SlowSecondAgentLLM(
        [
            plan(skill=None, title="PTT cancel"),
            FakeTurn(tool_calls=[call("get_prices", "call_C1", symbol="PTT")]),
            FakeTurn(text="หยุดแล้ว ตอบเท่าที่มี: PTT ปิดที่ 34.5 บาท {cite}"),
        ]
    )
    services = research_services(settings, [], llm=llm)
    session = await sessions_repo.create_session("research")
    run_id = await services.runs.start(
        "research", {"text": "PTT ราคา", "skill": None, "history": []}, session_id=session.id
    )
    for _ in range(200):
        await asyncio.sleep(0.02)
        if sum(c.purpose == "agent" for c in llm.calls) >= 2:
            break
    assert await services.runs.cancel(run_id, wait=True)
    events = await envelopes(run_id, session.id)

    assert of_type(events, "run.finished")[0]["payload"]["status"] == "cancelled"
    assert of_type(events, "answer.final")
    save("cancel", events)


async def test_discovery_three_turns_ask_ask_identified(settings: Settings, install_services: Any) -> None:
    """(P17 §17.2) 5 Whys across three chat turns through the API: research + why 1/5 → ask-only
    why 2/5 → root goal understood (chain reset in the session, goal remembered, a lesson distilled).
    Recorded as ``frontend/tests/fixtures/research-discovery.jsonl`` (all three runs)."""
    import httpx

    from app.api.main import app
    from app.domain.memory.schemas import DiscoveryAssessment, PrivacyVerdict
    from tests.harness.memory.helpers import facts, lesson, lessons

    q1, q2 = "ถ้าได้ถือ PTT แล้วจะช่วยอะไรคุณ?", "เงินที่โตขึ้นนั้นตั้งใจจะใช้ทำอะไรครับ?"
    goal_text = "เก็บเงินดาวน์บ้านภายในปี 2028"

    def research(final: str) -> list[Any]:
        return [
            FakeTurn(tool_calls=[call("get_prices", "call_D1", symbol="PTT")]),
            FakeTurn(text="Brief: PTT close"),
            FakeTurn(text=final),
        ]

    llm = CitingFakeLLM(
        [
            plan(title="อยากซื้อ PTT"),
            DiscoveryAssessment(request_type="buy_single", confidence=0.2, next_question=q1),
            *research("PTT ปิดที่ 34.5 บาท {cite} ข้อมูลนี้ไม่ใช่คำแนะนำการลงทุน"),
            facts(),
            plan(),
            DiscoveryAssessment(answers_pending_question=True, confidence=0.5, next_question=q2),
            f"เข้าใจครับ อยากให้เงินโตขึ้น {q2} ถ้าไม่สะดวกตอบ กดข้ามได้เลยครับ",
            facts(),
            plan(),
            DiscoveryAssessment.model_validate(
                {
                    "answers_pending_question": True,
                    "root_goal_identified": True,
                    "confidence": 0.85,
                    "stop_reason": "root_goal",
                    "root_goal": {"category": "home", "horizon_years": 2, "text": goal_text},
                    "memory_candidates": [
                        {
                            "category": "goal",
                            "text": f"ผู้ใช้{goal_text}",
                            "key": "goal:home_2028",
                            "confidence": 0.9,
                        }
                    ],
                }
            ),
            *research("PTT ปิดที่ 34.5 บาท {cite} ข้อมูลนี้ไม่ใช่คำแนะนำการลงทุน"),
            facts(),
            lessons(lesson("Tie a single-stock idea to the user's dated goal")),
            PrivacyVerdict(identifies_person_or_holdings=False),
        ]
    )
    services = install_services(research_services(settings, [], llm=llm, long_term_memory=True))
    # something to recall on turn 1: a memory item the question's FTS finds (no LLM — no
    # neighbour → plain ADD) and a research lesson in the bank (top-1 is always injected)
    seeded = await services.reasoning_bank._insert_or_merge(
        lesson("Answer with data first, then ask why"),
        kind="coaching",
        signal="success",
        score=0.8,
        tags={"workflow": "research"},
        model=None,
        source_run_id=None,
        user_id="usr_default",
    )
    all_events: list[dict[str, Any]] = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        added = await c.post("/api/memory", json={"category": "preference", "text": "ผู้ใช้ชอบหุ้นปันผลอย่าง PTT"})
        assert added.status_code == 200, added.text
        mem_id = added.json()["changes"][0]["item"]["id"]
        sid = (await c.post("/api/sessions", json={"kind": "research"})).json()["id"]

        async def turn(text: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            resp = await c.post(f"/api/sessions/{sid}/messages", json={"text": text})
            assert resp.status_code == 200, resp.text
            run_id = resp.json()["run_id"]
            await services.runs.wait(run_id)
            events = await envelopes(run_id, sid)
            all_events.extend(events)
            return events, (await c.get(f"/api/sessions/{sid}")).json()

        events, detail = await turn("ผมอยากซื้อ PTT")
        recalled = of_type(events, "memory.recalled")[0]["payload"]
        assert [i["id"] for i in recalled["items"]] == [mem_id]
        assert [r["id"] for r in recalled["reasoning"]] == [seeded.id]
        answer = of_type(events, "answer.final")[0]["payload"]
        assert answer["discovery"] == {"whys_asked": 1, "max": 5, "question": q1}
        assert detail["discovery"]["whys_asked"] == 1 and detail["discovery"]["chain"] == [{"q": q1, "a": ""}]
        run1 = of_type(events, "run.started")[0]["payload"]["input"]
        assert run1["user_id"] == "usr_default" and run1["discovery"] is None

        events, detail = await turn("อยากให้เงินโตขึ้น")
        assert "write_question" in [e["node"] for e in of_type(events, "node.started")]
        assert of_type(events, "answer.final")[0]["payload"]["discovery"]["whys_asked"] == 2
        assert detail["discovery"]["chain"] == [{"q": q1, "a": "อยากให้เงินโตขึ้น"}, {"q": q2, "a": ""}]
        # the second turn's assessment saw the chain from the session
        assess_call = [x for x in llm.calls if x.schema == "DiscoveryAssessment"][1]
        assert q1 in assess_call.messages[-1].content

        events, detail = await turn(goal_text)
        assessed = of_type(events, "discovery.assessed")[0]["payload"]
        assert assessed["root_goal_identified"] and assessed["root_goal"]["text"] == goal_text
        assert of_type(events, "answer.final")[0]["payload"]["discovery"] is None
        change = of_type(events, "memory.updated")[0]["payload"]["changes"][0]
        assert change["category"] == "goal" and change["status"] == "active"
        assert of_type(events, "reasoning.distilled")
        d = detail["discovery"]
        assert d["whys_asked"] == 0 and d["chain"] == [] and d["root_goal"]["text"] == goal_text
        assert d["root_goal_identified"] is True and d["stop_reason"] == "root_goal"
        assert detail["messages"][-1]["meta"]["memory_changes"][0]["category"] == "goal"
    goals = await services.user_memory.list_items("usr_default", statuses=["active"], category="goal")
    assert [g.key for g in goals] == ["goal:home_2028"]
    if os.environ.get("RECORD_FE_FIXTURES") == "1":
        lines = [json.dumps(e, ensure_ascii=False) for e in all_events]
        (FIXTURES / "research-discovery.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
