"""Sessions / messages / skills / workflow-graph API with FakeLLM injected (P07 §7.8)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.api.main import app
from app.harness.llm.fake import FakeTurn
from app.settings import Settings
from tests.harness.research.helpers import CitingFakeLLM, plan, research_services
from tests.harness.test_runs_api import _stream

pytestmark = pytest.mark.usefixtures("db")


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=20)


async def test_session_message_sse_until_answer(settings: Settings, install_services: Any) -> None:
    from app.domain.ports import ToolCallReq

    llm = CitingFakeLLM(
        [
            plan(skill="earnings-review", title="สรุปงบ AAPL"),
            FakeTurn(tool_calls=[ToolCallReq(id="call_A1", name="get_prices", args={"symbol": "AAPL"})]),
            FakeTurn(text="brief"),
            FakeTurn(text="AAPL 34.5 {cite} ข้อมูลนี้ไม่ใช่คำแนะนำการลงทุน"),
            plan(title="ต่อ"),
            FakeTurn(text="brief 2"),
            FakeTurn(text="ตอบต่อ"),
        ]
    )
    install_services(research_services(settings, [], llm=llm))
    async with client() as c:
        created = await c.post("/api/sessions", json={"kind": "research"})
        assert created.status_code == 200
        sid = created.json()["id"]

        resp = await c.post(
            f"/api/sessions/{sid}/messages", json={"text": "สรุปงบล่าสุดของ AAPL", "skill": "earnings-review"}
        )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]

        events = await _stream(c, run_id)
        kinds = [e["event"] for e in events]
        assert kinds[0] == "run.started" and kinds[-1] == "run.finished"
        assert "node.started" in kinds and "tool.started" in kinds and "context.usage" in kinds
        answer = next(e for e in events if e["event"] == "answer.final")["data"]["payload"]
        assert answer["citations"] and "[1]" in answer["markdown"]

        detail = (await c.get(f"/api/sessions/{sid}")).json()
        assert detail["session"]["title"] == "สรุปงบ AAPL"
        assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
        assert detail["messages"][0]["run_id"] == run_id
        assert detail["messages"][1]["meta"]["citations"]
        assert detail["runs"][0]["status"] == "completed"
        assert detail["runs"][0]["usage"]["llm_calls"] == 4
        assert "history" not in detail["runs"][0]["input"]

        listed = (await c.get("/api/sessions", params={"kind": "research"})).json()["sessions"]
        assert listed[0]["id"] == sid and listed[0]["last_run"]["status"] == "completed"

        # the second turn carries the first Q/A as history
        run2 = (await c.post(f"/api/sessions/{sid}/messages", json={"text": "แล้วไตรมาสก่อน?"})).json()[
            "run_id"
        ]
        await _stream(c, run2)
        agent_call = next(
            x for x in llm.calls if x.purpose == "agent" and "แล้วไตรมาสก่อน" in x.messages[-1].content
        )
        roles = [m.role for m in agent_call.messages]
        assert roles[:3] == ["system", "user", "assistant"]


async def test_message_validation_errors(settings: Settings, install_services: Any) -> None:
    install_services(research_services(settings, [plan(), "b", "a"]))
    async with client() as c:
        assert (await c.get("/api/sessions/ses_nope")).status_code == 404
        sid = (await c.post("/api/sessions", json={"kind": "research"})).json()["id"]
        bad = await c.post(f"/api/sessions/{sid}/messages", json={"text": "x", "skill": "no-such-skill"})
        assert bad.status_code == 400 and "unknown skill" in bad.json()["error"]["message"]
        x = await c.post(f"/api/sessions/{sid}/messages", json={"text": "x", "skill": "x-research"})
        assert x.status_code == 400 and "not available" in x.json()["error"]["message"]
        pf = (await c.post("/api/sessions", json={"kind": "portfolio"})).json()["id"]
        assert (await c.post(f"/api/sessions/{pf}/messages", json={"text": "x"})).status_code == 400
        empty = await c.post(f"/api/sessions/{sid}/messages", json={"text": ""})
        assert empty.status_code == 422


async def test_skills_endpoint_reports_availability(settings: Settings, install_services: Any) -> None:
    install_services(research_services(settings, []))
    async with client() as c:
        skills = {s["name"]: s for s in (await c.get("/api/skills")).json()}
    assert len(skills) == 12
    assert skills["x-research"]["available"] is False and skills["x-research"]["missing"] == ["x_api"]
    assert skills["company-valuation"] == {
        **skills["company-valuation"],
        "available": True,
        "category": "analysis",
        "role": "valuation_specialist",
    }
    assert {s["category"] for s in skills.values()} == {
        "research",
        "analysis",
        "data_processing",
        "reporting",
    }


async def test_workflow_graph(settings: Settings, install_services: Any) -> None:
    install_services(research_services(settings, []))
    async with client() as c:
        graph = (await c.get("/api/workflows/research/graph")).json()
        assert [n["id"] for n in graph["nodes"]] == [
            "plan",
            "retrieve_analyze",
            "verify",
            "synthesize_report",
        ]
        assert len(graph["edges"]) == 3
        assert (await c.get("/api/workflows/nope/graph")).status_code == 404


async def test_delete_session_removes_runs_events_provenance_and_scratchpad(
    settings: Settings, install_services: Any
) -> None:
    from app.domain.ports import ToolCallReq
    from app.harness.storage import events_repo, runs_repo, sessions_repo

    llm = CitingFakeLLM(
        [
            plan(title="ลบได้"),
            FakeTurn(tool_calls=[ToolCallReq(id="call_D1", name="get_prices", args={"symbol": "PTT"})]),
            FakeTurn(text="brief"),
            FakeTurn(text="PTT 34.5 {cite}"),
        ]
    )
    services = install_services(research_services(settings, [], llm=llm))
    async with client() as c:
        sid = (await c.post("/api/sessions", json={"kind": "research"})).json()["id"]
        keep = (await c.post("/api/sessions", json={"kind": "research"})).json()["id"]
        run_id = (await c.post(f"/api/sessions/{sid}/messages", json={"text": "PTT"})).json()["run_id"]
        await _stream(c, run_id)
        scratchpad = settings.scratchpad_dir / f"{run_id}.jsonl"
        assert scratchpad.exists() and await services.provenance.list(run_id)

        resp = await c.delete(f"/api/sessions/{sid}")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"deleted": sid, "runs": 1}

        assert (await c.get(f"/api/sessions/{sid}")).status_code == 404
        assert await runs_repo.get_run(run_id) is None
        assert await events_repo.events_after(run_id, 0) == []
        assert await services.provenance.list(run_id) == []
        assert await sessions_repo.list_messages(sid) == []
        assert not scratchpad.exists()
        listed = [s["id"] for s in (await c.get("/api/sessions")).json()["sessions"]]
        assert listed == [keep]
        assert (await c.delete(f"/api/sessions/{sid}")).status_code == 404


async def test_delete_session_refused_while_a_run_is_live(settings: Settings, install_services: Any) -> None:
    import asyncio

    from app.domain.ports import LLMMessage, LLMResponse

    class Slow(CitingFakeLLM):
        async def complete(
            self, messages: list[LLMMessage], *, tier: Any, tools: Any = None, purpose: str
        ) -> LLMResponse:
            if purpose == "agent":
                await asyncio.sleep(30)
            return await super().complete(messages, tier=tier, tools=tools, purpose=purpose)

    services = install_services(research_services(settings, [], llm=Slow([plan(), FakeTurn(text="ok")])))
    async with client() as c:
        sid = (await c.post("/api/sessions", json={"kind": "research"})).json()["id"]
        run_id = (await c.post(f"/api/sessions/{sid}/messages", json={"text": "PTT"})).json()["run_id"]
        resp = await c.delete(f"/api/sessions/{sid}")
        assert resp.status_code == 409
        await services.runs.cancel(run_id, wait=True)
        assert (await c.delete(f"/api/sessions/{sid}")).status_code == 200


async def test_llm_providers_lists_key_status(settings: Settings, install_services: Any) -> None:
    from pydantic import SecretStr

    keyed = settings.model_copy(update={"groq_api_key": SecretStr("gsk_SECRET_do_not_leak")})
    install_services(research_services(keyed, [], llm=CitingFakeLLM([])))
    async with client() as c:
        body = (await c.get("/api/llm/providers")).json()
    providers = {p["id"]: p for p in body["providers"]}
    assert providers["groq"]["configured"] is True
    assert providers["groq"]["models"][0] == "openai/gpt-oss-120b"
    assert providers["google"]["configured"] is False
    assert providers["google"]["env_hint"].startswith("GOOGLE_API_KEY")
    assert "SECRET" not in str(body)  # the key value never leaves the server


async def test_message_with_model_choice(settings: Settings, install_services: Any) -> None:
    from pydantic import SecretStr

    keyed = settings.model_copy(update={"groq_api_key": SecretStr("k")})
    chosen = CitingFakeLLM([plan(title="เลือกโมเดล"), FakeTurn(text="brief"), FakeTurn(text="ok")])
    picked: list[tuple[str, str]] = []

    def for_choice(provider: str, model: str) -> Any:
        picked.append((provider, model))
        return chosen

    services = research_services(keyed, [], llm=CitingFakeLLM([]))
    services.llm_for_choice = for_choice
    install_services(services)
    async with client() as c:
        sid = (await c.post("/api/sessions", json={"kind": "research"})).json()["id"]

        missing = await c.post(
            f"/api/sessions/{sid}/messages",
            json={"text": "PTT", "llm": {"provider": "gemini", "model": "gemini-3.8-flash"}},
        )
        assert missing.status_code == 400
        assert "GOOGLE_API_KEY" in missing.json()["error"]["message"]
        unknown = await c.post(
            f"/api/sessions/{sid}/messages", json={"text": "PTT", "llm": {"provider": "nope", "model": "x"}}
        )
        assert unknown.status_code == 400

        resp = await c.post(
            f"/api/sessions/{sid}/messages",
            json={"text": "PTT", "llm": {"provider": "groq", "model": "openai/gpt-oss-20b"}},
        )
        assert resp.status_code == 200, resp.text
        await _stream(c, resp.json()["run_id"])
        detail = (await c.get(f"/api/sessions/{sid}")).json()

    assert picked == [("groq", "openai/gpt-oss-20b")]
    assert chosen.calls and not services.llm.calls  # the default LLM was not used
    assert detail["messages"][0]["meta"]["llm"] == "groq:openai/gpt-oss-20b"
    assert detail["runs"][0]["input"]["llm"] == {"provider": "groq", "model": "openai/gpt-oss-20b"}
