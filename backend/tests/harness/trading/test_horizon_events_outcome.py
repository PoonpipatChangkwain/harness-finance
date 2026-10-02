"""Horizon-driven bars, economic-calendar event risk, time stop / invalidation, and settling an
approved trade into a lesson the next run reads."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.domain.workflows.spec import ApprovalResponse
from app.domain.workflows.trade_outcome import OutcomeError, TradeOutcomeInput
from app.harness.llm.fake import FakeLLM
from app.harness.runtime.services import HarnessServices
from app.harness.state.memory import MemoryStore
from app.harness.storage import approvals_repo, runs_repo
from app.harness.workflow.runner import answer_approval
from app.harness.workflow.trading import MemoryDecisionLog, settle_trade
from app.settings import Settings
from tests.harness.trading.helpers import (
    EVENTS,
    SNAPSHOT_CALLS,
    analyst_turns,
    debate_turns,
    event,
    events_of,
    happy_script,
    of,
    pm,
    proposal,
    research_plan,
    risk_turns,
    trading_services,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    SNAPSHOT_CALLS.clear()
    monkeypatch.setitem(EVENTS, "rows", [])


async def _start(services: HarnessServices, **inp: Any) -> str:
    run_id = await services.runs.start("trading", {"symbol": "PTT", **inp})
    await services.runs.wait(run_id)
    return run_id


async def _approve(services: HarnessServices, run_id: str) -> None:
    pending = await approvals_repo.pending_for_run(run_id)
    assert pending is not None
    await answer_approval(bus=services.bus, run_manager=services.runs, approval_id=pending.id,
                          response=ApprovalResponse(outcome="approved"))  # fmt: skip
    await services.runs.wait(run_id)


def _intraday_script() -> list[Any]:
    p = proposal()
    p.time_stop_bars = 12
    p.invalidation = "a 15m close back below 34.2"
    return [*analyst_turns(4), *debate_turns(), research_plan(), p, *risk_turns(), pm()]


async def test_intraday_uses_15m_bars_with_daily_context_and_its_time_stop(settings: Settings) -> None:
    llm = FakeLLM([*_intraday_script(), "narrative"])
    services = trading_services(settings, llm)
    run_id = await _start(services, horizon="intraday")

    assert [c["interval"] for c in SNAPSHOT_CALLS] == ["15m", "1d"]  # horizon bars, then higher timeframe
    card = of(await events_of(run_id), "approval.required")[0]["proposal"]
    assert card["interval"] == "15m" and card["horizon"] == "intraday"
    assert card["stop"] == pytest.approx(34.5 - 2 * 0.06)  # ATR of the 15-minute bars, not daily
    assert card["time_stop_bars"] == 12
    assert card["time_stop_at"] is not None and card["invalidation"] == "a 15m close back below 34.2"
    # every agent sees the playbook and the higher timeframe
    trader_call = next(
        c for c in llm.calls if c.purpose == "structured" and "trade proposal" in c.messages[-1].content
    )
    prompt = trader_call.messages[-1].content
    assert "Horizon: intraday — analyse 15-minute bars" in prompt and "HIGHER TIMEFRAME (daily" in prompt


async def test_missing_time_stop_takes_the_horizon_default(settings: Settings) -> None:
    services = trading_services(settings, FakeLLM([*happy_script(4), "narrative"]))
    run_id = await _start(services, horizon="swing")
    card = of(await events_of(run_id), "approval.required")[0]["proposal"]
    assert card["interval"] == "1h" and card["time_stop_bars"] == 48


async def test_high_impact_event_inside_the_guard_warns_and_reaches_every_agent(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(EVENTS, "rows", [event(1.5, title="Non-Farm Employment Change")])
    llm = FakeLLM([*_intraday_script(), "narrative"])
    services = trading_services(settings, llm)
    run_id = await _start(services, horizon="intraday")

    required = of(await events_of(run_id), "approval.required")[0]
    rule = next(r for r in required["policy_results"] if r["rule_id"] == "no_high_impact_event_soon")
    assert rule["passed"] is False and rule["severity"] == "warn"  # a human decides; not blocked
    bull = next(c for c in llm.calls if c.purpose == "debate")
    assert "HIGH USD Non-Farm Employment Change" in bull.messages[-1].content


async def test_unavailable_calendar_is_said_not_assumed_quiet(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(EVENTS, "rows", None)
    llm = FakeLLM([*happy_script(4), "narrative"])
    services = trading_services(settings, llm)
    run_id = await _start(services)
    required = of(await events_of(run_id), "approval.required")[0]
    rule = next(r for r in required["policy_results"] if r["rule_id"] == "no_high_impact_event_soon")
    assert rule["passed"] is False and "unavailable" in rule["message"]
    assert (
        "EVENT RISK: calendar unavailable"
        in next(c for c in llm.calls if c.purpose == "debate").messages[-1].content
    )


async def test_settled_outcome_computes_r_and_becomes_the_next_runs_lesson(settings: Settings) -> None:
    memory = MemoryStore()
    llm = FakeLLM([*happy_script(4), "narrative", "The ATR stop was too tight for the open; widen it."])
    services = trading_services(settings, llm, memory)
    run_id = await _start(services)
    await _approve(services, run_id)

    # long 34.5, stop 34.5 - 1.2 = 33.3; stopped out → -1R
    result = await settle_trade(services, run_id, TradeOutcomeInput(outcome="sl", note="gap down at open"))
    assert result["outcome"]["exit_price"] == pytest.approx(33.3)
    assert result["outcome"]["r_multiple"] == pytest.approx(-1.0)
    assert result["reflection"].startswith("The ATR stop was too tight")
    record = await services.provenance.get(result["outcome"]["provenance_ids"][0])
    assert record is not None and (record.calculation or "").startswith("compute.risk.realized_r")
    reflect_call = llm.calls[-1]
    assert reflect_call.purpose == "reflection" and "stop-loss hit" in reflect_call.messages[-1].content

    # a TP on the second target: (39 - 34.5) / 1.2 = 3.75R; re-settling overwrites
    llm.push("Target 2 was reachable within the swing window.")
    result = await settle_trade(services, run_id, TradeOutcomeInput(outcome="tp", target_index=1))
    assert result["outcome"]["r_multiple"] == pytest.approx(3.75)

    lessons = await MemoryDecisionLog(memory).lessons("PTT.BK")
    assert len(lessons) == 1 and "+3.75R" in lessons[0] and "Target 2 was reachable" in lessons[0]

    # the next run on the symbol reads it in the bull/bear debate
    llm.push(*happy_script(4))
    run2 = await _start(services)
    run = await runs_repo.get_run(run2)
    assert run is not None and run.status == "waiting_approval"
    bull = [c for c in llm.calls if "Give your bull argument now" in c.messages[-1].content][-1]
    assert "Lessons from past trades" in bull.messages[-1].content and "+3.75R" in bull.messages[-1].content
    pm_call = [c for c in llm.calls if "Return your final decision" in c.messages[-1].content][-1]
    assert "Lessons from past trades" in pm_call.messages[-1].content
    # the trader, who sets entry/stop/targets, reads them too
    trader_call = [c for c in llm.calls if "Return your trade proposal" in c.messages[-1].content][-1]
    assert "Lessons from past trades" in trader_call.messages[-1].content


async def test_the_traders_note_reaches_next_runs_verbatim_even_without_a_reflection(
    settings: Settings,
) -> None:
    memory = MemoryStore()
    llm = FakeLLM([*happy_script(4), "narrative", "Took the stop on a gap."])
    services = trading_services(settings, llm, memory)
    run_id = await _start(services)
    await _approve(services, run_id)
    note = "R:R ควรมากกว่า 1 เสมอถึงจะเข้า"
    await settle_trade(services, run_id, TradeOutcomeInput(outcome="sl", note=note))
    lessons = await MemoryDecisionLog(memory).lessons("PTT.BK")
    assert len(lessons) == 1 and "Took the stop on a gap." in lessons[0] and f'"{note}"' in lessons[0]

    # cancelled writes no reflection, but a note still teaches
    await settle_trade(services, run_id, TradeOutcomeInput(outcome="cancelled", note=note))
    lessons = await MemoryDecisionLog(memory).lessons("PTT.BK")
    assert len(lessons) == 1 and note in lessons[0] and "cancelled before entry" in lessons[0]


async def test_lessons_of_other_symbols_follow_this_symbols(settings: Settings) -> None:
    memory = MemoryStore()
    log = MemoryDecisionLog(memory)

    async def settled(symbol: str, run: str, at: str, lesson: str) -> None:
        await memory.put(
            "trade_decisions",
            f"{symbol}:2026-09-2{at}:{run}",
            {"symbol": symbol, "date": f"2026-09-2{at}", "card": {"side": "long"}, "reflection": lesson,
             "outcome": {"label": "stop-loss hit", "settled_at": f"2026-09-2{at}T00:00:00Z", "note": ""}},
        )  # fmt: skip

    await settled("PTT.BK", "r1", "1", "ptt old")
    await settled("PTT.BK", "r2", "2", "ptt new")
    await settled("AAPL", "r3", "3", "aapl")
    await settled("XAUUSD", "r4", "4", "gold")
    await settled("EURUSD=X", "r5", "5", "euro")

    assert [x.rsplit(" ", 1)[-1] for x in await log.lessons("PTT.BK")] == ["old", "new"]
    got = await log.lessons("PTT.BK", 1, 2)
    assert got[0].endswith("ptt new") and got[0].startswith("[PTT.BK")
    assert [x.split(" ", 1)[0] for x in got[1:]] == ["[XAUUSD", "[EURUSD=X"]  # the latest two others

    # a run dated 2026-09-23 must not learn from trades that settled on/after that day
    early = await log.lessons("PTT.BK", 3, 3, before=date(2026, 9, 23))
    assert [x.rsplit(" ", 1)[-1] for x in early] == ["old", "new"]  # AAPL settled 09-23: excluded
    assert await log.lessons("PTT.BK", before=date(2026, 9, 21)) == []

    # an undated settlement cannot be proven past: dropped from a dated run, kept in a live one
    await memory.put(
        "trade_decisions",
        "PTT.BK:2026-09-20:r0",
        {"symbol": "PTT.BK", "date": "2026-09-20", "card": {}, "reflection": "undated",
         "outcome": {"label": "stop-loss hit", "note": ""}},
    )  # fmt: skip
    assert not any("undated" in x for x in await log.lessons("PTT.BK", before=date(2026, 9, 30)))
    assert any("undated" in x for x in await log.lessons("PTT.BK"))


async def test_outcome_errors(settings: Settings) -> None:
    memory = MemoryStore()
    services = trading_services(settings, FakeLLM([*happy_script(4), "narrative"]), memory)
    run_id = await _start(services)
    await _approve(services, run_id)
    with pytest.raises(OutcomeError, match="exit_price is required"):
        await settle_trade(services, run_id, TradeOutcomeInput(outcome="manual_exit"))
    with pytest.raises(OutcomeError, match="target"):
        await settle_trade(services, run_id, TradeOutcomeInput(outcome="tp", target_index=4))
    # cancelled: no R, no reflection call
    result = await settle_trade(services, run_id, TradeOutcomeInput(outcome="cancelled"))
    assert result["outcome"]["r_multiple"] is None and result["reflection"] is None
