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
