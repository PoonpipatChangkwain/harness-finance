"""(P17 §17.2) research workflow v2: memory recall, 5-Whys routing, learn — FakeLLM, no network."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.memory.schemas import DiscoveryAssessment, PrivacyVerdict
from app.domain.ports import ToolCallReq
from app.domain.workflows.research import RESEARCH_WORKFLOW, judge_research
from app.harness.llm.fake import FakeTurn, ScriptItem
from app.harness.memory.store import NewMemory
from app.harness.runtime.services import HarnessServices
from app.harness.storage import runs_repo, sessions_repo
from app.harness.workflow.research import next_session_discovery
from app.harness.workflow.validate import validate_spec
from app.settings import Settings
from tests.harness.memory.helpers import fact, facts, lesson, lessons
from tests.harness.research.helpers import CitingFakeLLM, events_of, of, plan, research_services, types

pytestmark = pytest.mark.usefixtures("db")

USER = "usr_default"
Q1 = "ดูเหมือนหุ้นตัวนี้น่าสนใจ ถ้าได้ถือแล้วจะช่วยอะไรคุณ?"


def assess(**kw: Any) -> DiscoveryAssessment:
    return DiscoveryAssessment.model_validate(kw)


def research_turns(final: str = "PTT ปิดที่ 34.5 บาท {cite} ข้อมูลนี้ไม่ใช่คำแนะนำการลงทุน") -> list[ScriptItem]:
    """Agent: one tool round + a brief, then the streamed final answer."""
    return [
        FakeTurn(tool_calls=[ToolCallReq(id="call_M1", name="get_prices", args={"symbol": "PTT"})]),
        FakeTurn(text="Brief: PTT close"),
        FakeTurn(text=final),
    ]


def distilled(title: str = "Check the horizon before sizing a single stock") -> list[ScriptItem]:
    return [lessons(lesson(title)), PrivacyVerdict(identifies_person_or_holdings=False)]


def services_with(settings: Settings, script: list[ScriptItem]) -> tuple[HarnessServices, CitingFakeLLM]:
    llm = CitingFakeLLM(script)
    return research_services(settings, [], llm=llm, long_term_memory=True), llm


async def run(
    services: HarnessServices,
    text: str = "PTT ราคาปัจจุบันเท่าไร",
    *,
    session_id: str | None = None,
    **inp: Any,
) -> tuple[str, list[dict[str, Any]]]:
    run_id = await services.runs.start(
        "research", {"text": text, "skill": None, "history": [], **inp}, session_id=session_id
    )
    await services.runs.wait(run_id)
    return run_id, await events_of(run_id)


def nodes(events: list[dict[str, Any]]) -> list[str]:
    return [p["node"] for p in of(events, "node.started")]


def schemas(llm: CitingFakeLLM) -> list[str]:
    return [c.schema or "" for c in llm.calls if c.method == "structured"]


def test_spec_is_valid() -> None:
    validate_spec(RESEARCH_WORKFLOW)


async def test_research_branch_learns_after_the_answer_in_order(settings: Settings) -> None:
    services, llm = services_with(
        settings,
        [
            plan(),
            assess(request_type="info", stop_reason="informational"),
            *research_turns(),
            facts(fact("ผู้ใช้ชอบลงทุนระยะยาวแบบเรื่อย ๆ")),
            *distilled(),
        ],
    )
    session = await sessions_repo.create_session("research")
    run_id, events = await run(services, session_id=session.id)

    assert of(events, "run.finished")[0]["status"] == "completed"
    assert nodes(events) == [
        "plan", "recall_memory", "assess_discovery", "retrieve_analyze", "verify", "synthesize_report",
        "learn",
    ]  # fmt: skip
    t = types(events)
    first_llm_after_plan = next(
        i for i, e in enumerate(events) if e["type"] == "llm.started" and e["node"] != "plan"
    )
    assert t.index("memory.recalled") < first_llm_after_plan  # recall comes before any non-routing LLM call
    assert t.index("memory.recalled") < t.index("discovery.assessed")
    assert t.index("answer.final") < t.index("memory.updated") < t.index("reasoning.distilled")
    assert {e["node"] for e in events if e["type"] in ("memory.updated", "reasoning.distilled")} == {"learn"}
    change = of(events, "memory.updated")[0]["changes"][0]
    assert change["op"] == "ADD" and change["category"] == "investment_style" and change["status"] == "active"
    assert of(events, "reasoning.distilled")[0]["items"][0]["kind"] == "strategy"
    answer = of(events, "answer.final")[0]
    assert answer["citations"] and answer["discovery"] is None
    assert schemas(llm) == [
        "ResearchPlan", "DiscoveryAssessment", "ExtractedFacts", "DistilledLessons", "PrivacyVerdict",
    ]  # fmt: skip
    # the distilled lesson is tagged with the workflow, scenario-less, and the plan's market
    item = (await services.reasoning_bank.list_items(workflow="research"))[0]
    assert item.tags == {"workflow": "research", "market": "SET"}
    # the assistant message carries what was learned; usage is billed to the run as "memory"
    messages = await sessions_repo.list_messages(session.id)
    assert messages[-1].meta["memory_changes"][0]["op"] == "ADD"
    record = await runs_repo.get_run(run_id)
    assert record is not None and record.usage["by_purpose"]["memory"]["calls"] == 4


async def test_recalled_profile_and_lessons_reach_the_agent_and_are_settled(settings: Settings) -> None:
    services, llm = services_with(
        settings,
        [
            plan(),
            assess(request_type="info", stop_reason="informational"),
            *research_turns(),
            facts(),
            *distilled("Quote the close with its date"),
        ],
    )
    await services.user_memory.add(
        USER, NewMemory(category="risk_tolerance", text="ผู้ใช้รับความเสี่ยงได้ปานกลาง", importance=8)
    )
    lesson_item = await services.reasoning_bank._insert_or_merge(
        lesson("Lead with the price before the indicators"),
        kind="strategy",
        signal="success",
        score=1.0,
        tags={"workflow": "research"},
        model=None,
        source_run_id=None,
        user_id=USER,
    )
    run_id, events = await run(services)

    recalled = of(events, "memory.recalled")[0]
    assert recalled["reasoning"] == [{"id": lesson_item.id, "title": lesson_item.title}]
    agent_system = next(c for c in llm.calls if c.purpose == "agent").messages[0].content
    assert "## INVESTOR PROFILE" in agent_system and "รับความเสี่ยงได้ปานกลาง" in agent_system
    assert "## LESSONS FROM PAST RUNS" in agent_system and lesson_item.title in agent_system
    agent_user = [m.content for m in next(c for c in llm.calls if c.purpose == "agent").messages[1:]]
    assert not any("INVESTOR PROFILE" in m for m in agent_user)  # system prompt, not the user turn
    final_system = next(c for c in llm.calls if c.purpose == "final").messages[0].content
    assert "## INVESTOR PROFILE" in final_system
    # recorded as used, then settled as a success by the rule-based judge
    item = await services.reasoning_bank.get(lesson_item.id)
    assert item is not None and item.use_count == 1 and item.success_count == 1
    uses = await services.reasoning_bank.uses(lesson_item.id)
    assert [(u.run_id, u.outcome) for u in uses] == [(run_id, "success")]
    assert not of(events, "memory.updated")  # nothing new to remember → no event


async def test_ask_only_writes_the_question_without_research(settings: Settings) -> None:
    services, llm = services_with(
        settings,
        [
            plan(),
            assess(request_type="other", answers_pending_question=True, confidence=0.4, next_question="Q2?"),
            "เข้าใจครับ อยากให้เงินโตขึ้น Q2? ถ้าไม่สะดวกตอบ กดข้ามได้",
            facts(),
        ],
    )
    prior = {"whys_asked": 1, "chain": [{"q": Q1, "a": ""}], "confidence": 0.3, "advice_mode": "normal"}
    run_id, events = await run(services, "อยากให้เงินโตขึ้น", discovery=prior)

    assert "retrieve_analyze" not in nodes(events) and "write_question" in nodes(events)
    assert not [c for c in llm.calls if c.purpose == "agent"]  # no research cost
    question_call = next(c for c in llm.calls if c.method == "stream")
    assert question_call.tier == "quick" and question_call.purpose == "final"
    answer = of(events, "answer.final")[0]
    assert answer["discovery"] == {"whys_asked": 2, "max": 5, "question": "Q2?"}
    assert answer["citations"] == [] and "Q2?" in answer["markdown"]
    assessed = of(events, "discovery.assessed")[0]
    assert assessed["whys_asked"] == 2 and assessed["next_question"] == "Q2?"
    assert not of(events, "reasoning.distilled")
    record = await runs_repo.get_run(run_id)
    assert record is not None and record.output is not None
    assert record.output["discovery"]["chain"] == [{"q": Q1, "a": "อยากให้เงินโตขึ้น"}, {"q": "Q2?", "a": ""}]
    assert record.output["markdown"] == answer["markdown"]


async def test_research_then_ask_ends_with_the_question_verbatim(settings: Settings) -> None:
    services, llm = services_with(
        settings,
        [
            plan(),
            assess(request_type="buy_single", confidence=0.3, next_question=Q1),
            *research_turns(),  # the model forgets the question: the harness appends it
            facts(),
        ],
    )
    _, events = await run(services, "ผมอยากซื้อ PTT")

    assert "retrieve_analyze" in nodes(events) and "write_question" not in nodes(events)
    final_system = next(c for c in llm.calls if c.purpose == "final").messages[0].content
    assert "## Closing question" in final_system and Q1 in final_system
    answer = of(events, "answer.final")[0]
    assert answer["markdown"].rstrip().endswith(Q1) and answer["markdown"].count("?") == 1
    assert answer["discovery"] == {"whys_asked": 1, "max": 5, "question": Q1}
    assert not of(events, "reasoning.distilled")  # goal not understood yet: not judged


@pytest.mark.parametrize(
    ("script_item", "inp", "stop_reason"),
    [
        (RuntimeError("model down"), {}, None),  # parse failure → research, never blocks
        (None, {"skip_discovery": True}, "resistance"),  # the skip button: no assessment call at all
        (
            assess(request_type="buy_single", confidence=0.2, next_question="Q6"),
            {"discovery": {"whys_asked": 5, "chain": [{"q": f"q{i}", "a": f"a{i}"} for i in range(5)]}},
            "max_whys",
        ),
    ],
    ids=["parse_failure", "skip_discovery", "max_whys"],
)
async def test_routes_that_fall_back_to_plain_research(
    settings: Settings, script_item: ScriptItem | None, inp: dict[str, Any], stop_reason: str | None
) -> None:
    script: list[ScriptItem] = [plan()]
    if script_item is not None:
        script.append(script_item)
    services, llm = services_with(settings, [*script, *research_turns(), facts()])
    run_id, events = await run(services, "ผมอยากซื้อ PTT", **inp)

    assert of(events, "run.finished")[0]["status"] == "completed"
    assert "retrieve_analyze" in nodes(events) and "write_question" not in nodes(events)
    assessed = of(events, "discovery.assessed")[0]
    assert assessed["stop_reason"] == stop_reason and assessed["next_question"] is None
    assert of(events, "answer.final")[0]["discovery"] is None
    if inp.get("skip_discovery"):
        assert "DiscoveryAssessment" not in schemas(llm)
    if script_item is not None and isinstance(script_item, BaseException):
        assert "discovery_unparsed" in [w["code"] for w in of(events, "warning")]
    record = await runs_repo.get_run(run_id)
    assert record is not None and record.output is not None
    assert record.output["discovery"]["route"] == "research"


async def test_urgency_stabilises_the_advice(settings: Settings) -> None:
    services, llm = services_with(
        settings,
        [
            plan(),
            assess(request_type="sell_all", stop_reason="urgency", confidence=0.1, next_question="ทำไม?"),
            *research_turns(),
            facts(),
        ],
    )
    _, events = await run(services, "ตลาดพังแล้ว ขายหมดเลยดีไหม โดน margin call")

    assessed = of(events, "discovery.assessed")[0]
    assert assessed["advice_mode"] == "stabilise" and assessed["stop_reason"] == "urgency"
    assert "write_question" not in nodes(events) and of(events, "answer.final")[0]["discovery"] is None
    agent_system = next(c for c in llm.calls if c.purpose == "agent").messages[0].content
    assert "ADVICE MODE: stabilise" in agent_system


async def test_learn_failure_is_a_warning_not_a_failed_run(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    services, _ = services_with(
        settings, [plan(), assess(request_type="info", stop_reason="informational"), *research_turns()]
    )

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("memory store is down")

    monkeypatch.setattr(services.memory_engine, "learn", boom)
    monkeypatch.setattr(services.reasoning_bank, "distill", boom)
    _, events = await run(services)

    assert of(events, "run.finished")[0]["status"] == "completed"
    codes = [w["code"] for w in of(events, "warning")]
    assert codes.count("memory_learn_failed") == 2
    learn_done = next(p for p in of(events, "node.finished") if p["node"] == "learn")
    assert learn_done["status"] == "done"


async def test_memory_off_makes_no_memory_calls(settings: Settings) -> None:
    llm = CitingFakeLLM([plan(), *research_turns()])
    services = research_services(settings, [], llm=llm)  # long_term_memory=False
    _, events = await run(services)

    assert of(events, "run.finished")[0]["status"] == "completed"
    assert not {"memory.recalled", "discovery.assessed", "memory.updated"} & set(types(events))
    assert [c.purpose for c in llm.calls] == ["structured", "agent", "agent", "final"]


def test_judge_research_rules() -> None:
    ok = {"citations": [{"marker": 1}], "unverified_numbers": [], "stop_reason": None}
    assert judge_research({"answer": ok, "discovery": {"request_type": "info"}}) is True
    assert judge_research({"answer": ok, "discovery": {"root_goal_identified": True}}) is True
    assert judge_research({"answer": ok, "discovery": {"request_type": "buy_single"}}) is None
    limited = {**ok, "stop_reason": "iterations: 10"}
    assert judge_research({"answer": limited, "discovery": {}}) is False
    assert judge_research({"answer": {**ok, "unverified_numbers": ["1", "2", "3", "4"]}}) is False
    assert judge_research({"question": {"markdown": "Q?"}}) is None  # ask-only turns are not judged


def test_session_discovery_resets_when_understood_or_stopped() -> None:
    chain = [{"q": "q1", "a": "a1"}]
    ongoing = next_session_discovery({"whys_asked": 1, "chain": chain, "confidence": 0.4})
    assert ongoing["whys_asked"] == 1 and ongoing["chain"] == chain
    goal = {"text": "บ้านปี 2028", "category": "home"}
    done = next_session_discovery(
        {"whys_asked": 3, "chain": chain, "root_goal_identified": True, "root_goal": goal}
    )
    assert done["whys_asked"] == 0 and done["chain"] == [] and done["root_goal"] == goal
    for reason in ("max_whys", "resistance"):
        stopped = next_session_discovery({"whys_asked": 5, "chain": chain, "stop_reason": reason})
        assert stopped["whys_asked"] == 0 and stopped["chain"] == []


async def test_post_message_passes_skip_user_and_session_discovery(
    settings: Settings, install_services: Any
) -> None:
    import httpx

    from app.api.main import app

    llm = CitingFakeLLM([plan(), *research_turns()])
    services = install_services(research_services(settings, [], llm=llm))
    session = await sessions_repo.create_session("research")
    state = {
        "whys_asked": 2,
        "chain": [{"q": "q1", "a": "a1"}, {"q": "q2", "a": ""}],
        "root_goal_identified": False,
        "confidence": 0.4,
        "advice_mode": "normal",
    }
    await sessions_repo.update_meta(session.id, {"discovery": state})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        detail = (await c.get(f"/api/sessions/{session.id}")).json()
        assert detail["discovery"] == {**state, "root_goal": None, "stop_reason": None}
        resp = await c.post(
            f"/api/sessions/{session.id}/messages",
            json={"text": "วิเคราะห์เลย", "skip_discovery": True},
            headers={"X-User-Id": "usr_alice"},
        )
        assert resp.status_code == 200, resp.text
        await services.runs.wait(resp.json()["run_id"])
        bad = await c.post(
            f"/api/sessions/{session.id}/messages", json={"text": "x"}, headers={"X-User-Id": "alice"}
        )
        assert bad.status_code == 400
    record = await runs_repo.get_run(resp.json()["run_id"])
    assert record is not None
    assert record.input["skip_discovery"] is True and record.input["user_id"] == "usr_alice"
    assert record.input["discovery"] == state
    user_message = (await sessions_repo.list_messages(session.id))[0]
    assert user_message.meta["skip_discovery"] is True
    # memory off: the session's 5-Whys state is left as it was
    assert (await sessions_repo.get_session(session.id)).meta["discovery"] == state  # type: ignore[union-attr]
