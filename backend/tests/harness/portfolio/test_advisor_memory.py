"""(P17 §17.2) ``portfolio_advisor`` v2: recall the investor profile, learn from approve/reject."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.memory.schemas import PrivacyVerdict
from app.domain.models.portfolio import portfolio_hash
from app.harness.llm.fake import FakeLLM, ScriptItem
from app.harness.memory.store import NewMemory
from app.harness.runtime.services import HarnessServices
from app.harness.storage import portfolio_repo, runs_repo
from app.settings import Settings
from tests.domain.portfolio.fakes import install
from tests.harness.memory.helpers import fact, facts, lesson, lessons
from tests.harness.portfolio.helpers import DEFAULT_ID, approve, portfolio_services, proposal, save_book
from tests.harness.trading.helpers import events_of, node_order, of

pytestmark = pytest.mark.usefixtures("db")

USER = "usr_default"
DIAGNOSES: list[ScriptItem] = ["risk: AAPL is 46% of the book", "allocation: trim AAPL, keep cash"]
PERMISSIVE = {"max_weight": 0.3, "max_turnover": 0.5}
GOAL = "ผู้ใช้อยากเกษียณภายในปี 2045 ด้วยรายได้จากเงินปันผล"


@pytest.fixture(autouse=True)
def market(monkeypatch: pytest.MonkeyPatch) -> Any:
    return install(monkeypatch)


async def _start(services: HarnessServices, **inp: Any) -> str:
    pf = await portfolio_repo.get_portfolio(DEFAULT_ID)
    run_input = {
        "portfolio_id": DEFAULT_ID,
        "risk_profile": "balanced",
        "portfolio_hash": portfolio_hash(pf) if pf else None,
        "constraints": PERMISSIVE,
        **inp,
    }
    run_id = await services.runs.start("portfolio_advisor", run_input)
    await services.runs.wait(run_id)
    return run_id


async def _services(settings: Settings, script: list[ScriptItem]) -> tuple[HarnessServices, FakeLLM]:
    await save_book()
    llm = FakeLLM(script)
    services = portfolio_services(settings, llm, long_term_memory=True)
    await services.user_memory.add(
        USER, NewMemory(category="goal", text=GOAL, key="goal:retire", importance=9)
    )
    return services, llm


async def test_profile_and_goal_from_memory_then_approve_distills_a_strategy(settings: Settings) -> None:
    services, llm = await _services(
        settings,
        [
            *DIAGNOSES,
            proposal({"PTT": 0.20, "AAPL": 0.20}, 0.60),
            lessons(lesson("Trim the largest single-stock weight first")),
            PrivacyVerdict(identifies_person_or_holdings=False),
        ],
    )
    run_id = await _start(services)  # no goal given: the advisor uses the one in memory

    events = await events_of(run_id)
    order = [n for n in node_order(events) if "." not in n]
    assert order.index("compute_analytics") < order.index("recall_memory") < order.index("diagnose")
    assert of(events, "memory.recalled")
    diagnose_prompts = [c.messages[-1].content for c in llm.calls if c.purpose == "agent"]
    assert len(diagnose_prompts) == 2
    assert all(
        "## INVESTOR PROFILE" in p and GOAL in p and "(from the user's memory)" in p for p in diagnose_prompts
    )
    propose = next(c for c in llm.calls if c.schema == "RebalanceProposal")
    assert "## INVESTOR PROFILE" in propose.messages[-1].content

    await approve(services, run_id)
    events = await events_of(run_id)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "completed" and run.output is not None
    assert f"Goal from your memory: {GOAL}" in run.output["report_md"]
    assert [n for n in node_order(events) if "." not in n][-2:] == ["report", "learn"]
    distilled = of(events, "reasoning.distilled")[0]["items"]
    assert distilled[0]["kind"] == "strategy"
    item = await services.reasoning_bank.get(distilled[0]["id"])
    assert item is not None and item.tags == {"workflow": "portfolio"} and item.source_run_id == run_id
    assert run.output["state"]["learned"]["success"] is True
    assert not of(events, "memory.updated")  # approving teaches the bank, not the user's memory


async def test_reject_with_a_comment_learns_the_constraint_and_a_pitfall(settings: Settings) -> None:
    services, llm = await _services(
        settings,
        [
            *DIAGNOSES,
            proposal({"PTT": 0.20, "AAPL": 0.20}, 0.60),
            facts(fact("ผู้ใช้ไม่ต้องการขาย PTT เด็ดขาด", category="constraint", key="no_sell:PTT")),
            lessons(lesson("Ask for no-sell constraints before proposing sells", kind="strategy")),
            PrivacyVerdict(identifies_person_or_holdings=False),
        ],
    )
    # the lesson recalled for this run is settled as a failure when the plan is rejected
    used = await services.reasoning_bank._insert_or_merge(
        lesson("Keep at least five percent cash"),
        kind="strategy",
        signal="success",
        score=1.0,
        tags={"workflow": "portfolio"},
        model=None,
        source_run_id=None,
        user_id=USER,
    )
    run_id = await _start(services, goal="เน้นปันผล")
    assert of(await events_of(run_id), "memory.recalled")[0]["reasoning"][0]["id"] == used.id

    await approve(services, run_id, outcome="rejected", comment="ไม่ขาย PTT เด็ดขาด")
    events = await events_of(run_id)
    change = of(events, "memory.updated")[0]["changes"][0]
    assert change["category"] == "constraint" and "PTT" in change["text"]
    extract = next(c for c in llm.calls if c.schema == "ExtractedFacts")
    assert "ไม่ขาย PTT เด็ดขาด" in extract.messages[-1].content
    distilled = of(events, "reasoning.distilled")[0]["items"]
    assert distilled[0]["kind"] == "pitfall"  # a failure always yields a pitfall
    settled = await services.reasoning_bank.get(used.id)
    assert settled is not None and settled.use_count == 1 and settled.success_count == 0
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "declined"
    assert "Goal from your memory" not in run.output["report_md"]  # the run's own goal wins


async def test_memory_off_keeps_the_advisor_as_before(settings: Settings) -> None:
    await save_book()
    llm = FakeLLM([*DIAGNOSES, proposal({"PTT": 0.20, "AAPL": 0.20}, 0.60)])
    services = portfolio_services(settings, llm)
    run_id = await _start(services)
    await approve(services, run_id)
    events = await events_of(run_id)
    assert not {"memory.recalled", "memory.updated", "reasoning.distilled"} & {e["type"] for e in events}
    assert all("INVESTOR PROFILE" not in c.messages[-1].content for c in llm.calls)
