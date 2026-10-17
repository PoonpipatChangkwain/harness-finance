"""End-to-end research workflow with FakeLLM + fake tools (P07 §7.8)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domain.data.base import Capability
from app.domain.ports import LLMMessage, LLMResponse, ToolCallReq
from app.domain.tools.base import ToolSpec
from app.harness.llm.fake import FakeLLM, FakeTurn
from app.harness.runtime.limits import RunLimits
from app.harness.storage import runs_repo, sessions_repo
from app.settings import Settings
from tests.harness.fake_tools import SymArgs, _prices
from tests.harness.research.helpers import (
    CitingFakeLLM,
    events_of,
    of,
    plan,
    research_services,
    run_research,
    types,
)

pytestmark = pytest.mark.usefixtures("db")


def call(name: str, **args: Any) -> ToolCallReq:
    call.n += 1  # type: ignore[attr-defined]
    return ToolCallReq(id=f"call_T{call.n}", name=name, args=args)  # type: ignore[attr-defined]


call.n = 0  # type: ignore[attr-defined]


def agent_calls(llm: FakeLLM) -> list[Any]:
    return [c for c in llm.calls if c.purpose == "agent"]


async def test_tool_call_then_final_answer_event_order(settings: Settings) -> None:
    llm = CitingFakeLLM(
        [
            plan(),
            FakeTurn(tool_calls=[call("get_prices", symbol="PTT"), call("compute_indicators", symbol="PTT")]),
            FakeTurn(text="Brief: PTT close 34.5, RSI 55.2"),
            FakeTurn(text="PTT ปิดที่ 34.5 บาท {cite} ข้อมูลนี้ไม่ใช่คำแนะนำการลงทุน"),
        ]
    )
    services = research_services(settings, [], llm=llm)
    session = await sessions_repo.create_session("research")
    run_id, events = await run_research(services, session_id=session.id)

    t = types(events)
    assert t[0] == "run.started" and t[-1] == "run.finished"
    nodes = [p["node"] for p in of(events, "node.started")]
    # (P17) memory off: recall_memory / assess_discovery / learn run but do nothing (no LLM call)
    assert nodes == [
        "plan", "recall_memory", "assess_discovery", "retrieve_analyze", "verify", "synthesize_report",
        "learn",
    ]  # fmt: skip
    # node → llm → tool (concurrent batch) → context.usage → answer.final
    i_node = t.index("node.started")
    i_llm = t.index("llm.started")
    i_tool = t.index("tool.started")
    i_usage = t.index("context.usage")
    i_answer = t.index("answer.final")
    assert i_node < i_llm < i_usage < i_tool < i_answer
    started = of(events, "tool.started")
    assert len({p["batch_id"] for p in started}) == 1  # both read-only calls ran as one batch
    purposes = [p["purpose"] for p in of(events, "llm.started")]
    assert purposes == ["structured", "agent", "agent", "final"]
    tool_events = [e for e in events if e["type"] == "tool.started"]
    assert {e["node"] for e in tool_events} == {"retrieve_analyze"}

    answer = of(events, "answer.final")[0]
    assert "[1]" in answer["markdown"] and answer["citations"][0]["marker"] == 1
    assert answer["unverified_numbers"] == []
    assert of(events, "run.finished")[0]["status"] == "completed"

    messages = await sessions_repo.list_messages(session.id)
    assert messages[-1].role == "assistant" and messages[-1].run_id == run_id
    assert (await sessions_repo.get_session(session.id)).title == "PTT วิเคราะห์"  # type: ignore[union-attr]
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.usage["by_purpose"]["agent"]["calls"] == 2
    assert run.output is not None and run.output["prompt_versions"] == {"research_lead": "research_lead.v2"}


async def test_max_iterations_graceful_final_and_guard(settings: Settings) -> None:
    llm = CitingFakeLLM(
        [
            plan(),
            FakeTurn(tool_calls=[call("get_prices", symbol="PTT")]),
            FakeTurn(tool_calls=[call("get_prices", symbol="AAPL")]),
            FakeTurn(text="คำตอบจากข้อมูลที่มี {cite}"),
        ]
    )
    services = research_services(settings, [], llm=llm)
    _, events = await run_research(services, limits=RunLimits(max_iterations=2))
    assert of(events, "guard.limit")[0]["kind"] == "iterations"
    assert len(agent_calls(llm)) == 2
    final_call = next(c for c in llm.calls if c.purpose == "final")
    assert "maximum number of steps" in final_call.messages[-1].content
    assert of(events, "answer.final")
    end = of(events, "run.finished")[0]
    assert end["status"] == "limited" and "iterations" in end["reason"]


async def test_tool_error_is_shown_to_llm_which_tries_another_tool(settings: Settings) -> None:
    llm = CitingFakeLLM(
        [
            plan(),
            FakeTurn(tool_calls=[call("boom", label="x")]),
            FakeTurn(tool_calls=[call("get_prices", symbol="PTT")]),
            FakeTurn(text="done"),
            FakeTurn(text="ok {cite}"),
        ]
    )
    services = research_services(settings, [], llm=llm)
    _, events = await run_research(services)
    second = agent_calls(llm)[1]
    tool_msgs = [m for m in second.messages if m.role == "tool"]
    assert tool_msgs and "RuntimeError: boom x" in tool_msgs[0].content
    statuses = [p["status"] for p in of(events, "tool.finished")]
    assert statuses == ["error", "ok"]
    assert of(events, "run.finished")[0]["status"] == "completed"


async def test_similar_and_fourth_calls_are_limited(settings: Settings) -> None:
    llm = CitingFakeLLM(
        [
            plan(),
            FakeTurn(tool_calls=[call("get_prices", symbol="PTT")]),
            FakeTurn(tool_calls=[call("get_prices", symbol="PTT")]),  # similar → blocked
            FakeTurn(tool_calls=[call("get_prices", symbol="AAPL"), call("get_prices", symbol="MSFT")]),
            FakeTurn(tool_calls=[call("get_prices", symbol="GOOG")]),  # 4th executed call → limit
            FakeTurn(text="done"),
            FakeTurn(text="ok {cite}"),
        ]
    )
    services = research_services(settings, [], llm=llm)
    _, events = await run_research(services)
    statuses = [p["status"] for p in of(events, "tool.finished")]
    assert statuses == ["ok", "limited", "ok", "ok", "limited"]
    kinds = [p["kind"] for p in of(events, "guard.limit")]
    assert kinds == ["similar_query", "tool_calls"]
    # the reminder about the near-limit tool reached the model
    assert any("<tool_usage_status>" in m.content for m in agent_calls(llm)[-1].messages if m.role == "user")


async def test_skill_called_twice_is_limited(settings: Settings) -> None:
    llm = CitingFakeLLM(
        [
            plan(),
            FakeTurn(tool_calls=[call("skill", skill="technical-analysis")]),
            FakeTurn(tool_calls=[call("skill", skill="technical-analysis")]),
            FakeTurn(text="done"),
            FakeTurn(text="ok"),
        ]
    )
    services = research_services(settings, [], llm=llm)
    _, events = await run_research(services)
    finished = [p for p in of(events, "tool.finished") if p["tool"] == "skill"]
    assert [p["status"] for p in finished] == ["ok", "limited"]


async def test_user_selected_scenario_is_preloaded(settings: Settings) -> None:
    llm = CitingFakeLLM([plan(skill="technical-analysis"), FakeTurn(text="brief"), FakeTurn(text="ok")])
    services = research_services(settings, [], llm=llm)
    _, events = await run_research(services, "PTT", skill="earnings-review")
    first = agent_calls(llm)[0]
    user = next(m for m in first.messages if m.role == "user")
    assert '<scenario name="earnings-review">' in user.content and "# Earnings Review" in user.content
    assert "equity_analyst" not in first.messages[0].content  # role prompt text, not the id
    assert "You are an Equity Research Analyst" in first.messages[0].content  # skill role wins over plan role
    skill_rows = [e for e in events if e["type"] == "tool.started" and e["payload"]["tool"] == "skill"]
    assert skill_rows and skill_rows[0]["payload"]["args"] == {"skill": "earnings-review"}
    assert of(events, "node.finished")[0]["summary"] == "skill earnings-review · role equity_analyst"


async def test_unverified_numbers_trigger_one_verify_pass(settings: Settings) -> None:
    llm = CitingFakeLLM(
        [
            plan(),
            FakeTurn(tool_calls=[call("get_prices", symbol="PTT")]),
            FakeTurn(text="Brief: 111.11, 222.22, 333.33 and 444.44"),
            FakeTurn(text="Brief corrected"),  # verify pass
            FakeTurn(text="PTT 34.5 {cite} แต่ 999.99 ไม่มีที่มา"),
        ]
    )
    services = research_services(settings, [], llm=llm)
    _, events = await run_research(services)
    calls = agent_calls(llm)
    assert len(calls) == 3  # tool round + brief + exactly one verify pass
    assert "not backed by any tool result" in calls[2].messages[-1].content
    assert "111.11" in calls[2].messages[-1].content
    answer = of(events, "answer.final")[0]
    assert answer["unverified_numbers"] == ["999.99"]
    verify_done = next(p for p in of(events, "node.finished") if p["node"] == "verify")
    assert verify_done["summary"] == "4 unverified in brief · re-checked once"


def _news_tool() -> ToolSpec:
    return ToolSpec(
        name="fake_news",
        description="news",
        args_model=SymArgs,
        category="data",
        requires=frozenset({Capability.NEWS}),
        fn=_prices,
    )


async def test_unconfigured_capability_tools_are_not_offered(settings: Settings) -> None:
    llm = CitingFakeLLM([plan(), FakeTurn(text="brief"), FakeTurn(text="ok")])
    services = research_services(
        settings,
        [],
        llm=llm,
        extra_tools=[_news_tool()],
        capability_available=lambda cap: cap != Capability.NEWS,
    )
    await run_research(services)
    offered = agent_calls(llm)[0].tools
    assert "fake_news" not in offered and "get_prices" in offered and "skill" in offered


class SlowAgentLLM(CitingFakeLLM):
    async def complete(
        self, messages: list[LLMMessage], *, tier: Any, tools: Any = None, purpose: str
    ) -> LLMResponse:
        if purpose == "agent":
            await asyncio.sleep(30)
        return await super().complete(messages, tier=tier, tools=tools, purpose=purpose)


async def test_cancel_is_graceful_with_an_answer(settings: Settings) -> None:
    llm = SlowAgentLLM([plan(), FakeTurn(text="หยุดแล้ว ตอบเท่าที่มี")])  # the agent call is aborted
    services = research_services(settings, [], llm=llm)
    run_id = await services.runs.start("research", {"text": "PTT", "history": []})
    for _ in range(100):
        await asyncio.sleep(0.02)
        if any(c.purpose == "agent" for c in llm.calls):
            break
    assert await services.runs.cancel(run_id, wait=True)
    events = await events_of(run_id)
    assert of(events, "answer.final")[0]["markdown"].startswith("หยุดแล้ว")
    final_call = next(c for c in llm.calls if c.purpose == "final")
    assert "stopped the research early" in final_call.messages[-1].content
    end = of(events, "run.finished")[0]
    assert end["status"] == "cancelled"


async def test_llm_error_in_agent_still_answers(settings: Settings) -> None:
    llm = CitingFakeLLM([plan(), RuntimeError("provider down"), FakeTurn(text="ขออภัย ข้อมูลไม่พอ")])
    services = research_services(settings, [], llm=llm)
    _, events = await run_research(services)
    assert of(events, "warning")[0]["code"] == "llm_error"
    assert of(events, "answer.final")
    assert of(events, "run.finished")[0]["status"] == "completed"


async def test_final_answer_failure_falls_back(settings: Settings) -> None:
    llm = CitingFakeLLM([plan(), FakeTurn(text="brief"), RuntimeError("stream down")])
    services = research_services(settings, [], llm=llm)
    _, events = await run_research(services)
    codes = [p["code"] for p in of(events, "warning")]
    assert "final_answer_failed" in codes
    assert "ไม่สามารถเขียนคำตอบ" in of(events, "answer.final")[0]["markdown"]


async def test_disclaimer_only_answer_is_retried(settings: Settings) -> None:
    llm = CitingFakeLLM(
        [
            plan(),
            FakeTurn(tool_calls=[call("get_prices", symbol="PTT")]),
            FakeTurn(text="brief"),
            FakeTurn(text="**ข้อมูลนี้ไม่ใช่คำแนะนำการลงทุน**"),
            FakeTurn(text="PTT ปิดที่ 34.5 บาท {cite}\n\nข้อมูลนี้ไม่ใช่คำแนะนำการลงทุน"),
        ]
    )
    services = research_services(settings, [], llm=llm)
    _, events = await run_research(services)
    assert [p["code"] for p in of(events, "warning")] == ["final_answer_hollow"]
    finals = [c for c in llm.calls if c.purpose == "final"]
    assert len(finals) == 2 and "only the disclaimer" in finals[1].messages[-1].content
    answer = of(events, "answer.final")[0]
    assert "PTT ปิดที่" in answer["markdown"] and answer["citations"]


async def test_hollow_answer_twice_falls_back_to_brief(settings: Settings) -> None:
    brief = "Risk level high: tech 49.6% of the book, NVDA drives most of the risk."
    llm = CitingFakeLLM(
        [
            plan(),
            FakeTurn(tool_calls=[call("get_prices", symbol="PTT")]),
            FakeTurn(text=brief),
            FakeTurn(text="ข้อมูลนี้ไม่ใช่คำแนะนำการลงทุน"),
            FakeTurn(text="---"),
        ]
    )
    services = research_services(settings, [], llm=llm)
    _, events = await run_research(services)
    markdown = of(events, "answer.final")[0]["markdown"]
    assert "ร่างผลวิจัย" in markdown and "NVDA drives most of the risk" in markdown


async def test_plan_failure_falls_back_to_defaults(settings: Settings) -> None:
    llm = CitingFakeLLM([RuntimeError("no structured"), FakeTurn(text="brief"), FakeTurn(text="ok")])
    services = research_services(settings, [], llm=llm)
    session = await sessions_repo.create_session("research")
    _, events = await run_research(services, "วิเคราะห์ หุ้น PTT ใน ปี นี้ อย่างละเอียด มาก", session_id=session.id)
    summary = of(events, "node.finished")[0]["summary"]
    assert summary == "skill auto · role research_lead"
    got = await sessions_repo.get_session(session.id)
    assert got is not None and got.title == "วิเคราะห์ หุ้น PTT ใน ปี นี้"


async def test_data_question_without_tool_calls_is_nudged_once(settings: Settings) -> None:
    llm = CitingFakeLLM(
        [
            plan(skill="technical-analysis"),
            FakeTurn(text="I will call get_market_snapshot…"),  # thinks about tools, calls none
            FakeTurn(tool_calls=[call("get_prices", symbol="PTT")]),
            FakeTurn(text="brief"),
            FakeTurn(text="PTT 34.5 {cite}"),
        ]
    )
    services = research_services(settings, [], llm=llm)
    _, events = await run_research(services)
    assert [p["code"] for p in of(events, "warning")] == ["no_tool_calls"]
    second = agent_calls(llm)[1]
    assert "You have not called any tool yet" in second.messages[-1].content
    assert of(events, "answer.final")[0]["citations"]


async def test_conceptual_question_is_not_nudged(settings: Settings) -> None:
    from app.domain.agents.schemas import ResearchPlan

    llm = CitingFakeLLM([ResearchPlan(title="P/E คืออะไร"), FakeTurn(text="P/E is price over EPS"), "ok"])
    services = research_services(settings, [], llm=llm)
    _, events = await run_research(services, "P/E คืออะไร")
    assert len(agent_calls(llm)) == 1 and not of(events, "warning")
