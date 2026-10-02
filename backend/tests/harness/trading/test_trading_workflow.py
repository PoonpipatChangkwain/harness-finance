"""Trading workflow end to end with FakeLLM (phase-10 §10.6)."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.agents.schemas import PortfolioDecision
from app.domain.models.decision import Rating, Verdict
from app.domain.models.symbol import AssetType
from app.domain.workflows.spec import ApprovalResponse
from app.domain.workflows.trading import (
    TRADING_WORKFLOW,
    select_analysts,
    should_continue_debate,
    should_continue_risk,
)
from app.harness.llm.fake import FakeLLM
from app.harness.runtime.services import HarnessServices
from app.harness.state.memory import MemoryStore
from app.harness.storage import approvals_repo, runs_repo
from app.harness.workflow.runner import answer_approval
from app.harness.workflow.validate import validate_spec
from app.settings import Settings
from tests.harness.trading.helpers import (
    QUOTES,
    TimedFakeLLM,
    analyst_turns,
    debate_turns,
    decision,
    events_of,
    happy_script,
    node_order,
    of,
    pm,
    proposal,
    research_plan,
    risk_turns,
    trading_services,
)

pytestmark = pytest.mark.usefixtures("db")

PRE_APPROVAL = [
    "resolve_symbol", "load_context", "analysts", "bull_researcher", "bear_researcher", "research_manager",
    "trader", "aggressive", "conservative", "neutral", "portfolio_manager", "policy_gate", "refresh_price",
    "approval",
]  # fmt: skip


async def _start(services: HarnessServices, **inp: Any) -> str:
    run_id = await services.runs.start("trading", {"symbol": "PTT", **inp})
    await services.runs.wait(run_id)
    return run_id


async def _approve(services: HarnessServices, run_id: str, outcome: str = "approved", **kw: Any) -> None:
    pending = await approvals_repo.pending_for_run(run_id)
    assert pending is not None
    await answer_approval(
        bus=services.bus, run_manager=services.runs, approval_id=pending.id,
        response=ApprovalResponse(outcome=outcome, **kw),  # type: ignore[arg-type]
    )  # fmt: skip
    await services.runs.wait(run_id)


def test_spec_is_valid_and_all_nodes_reachable() -> None:
    validate_spec(TRADING_WORKFLOW)


def test_default_analysts_by_asset_type() -> None:
    assert select_analysts(AssetType.STOCK, None) == ["market", "news", "fundamentals", "sentiment"]
    assert select_analysts(AssetType.FOREX, None) == ["market", "news", "macro"]
    assert select_analysts(AssetType.CRYPTO, None) == ["market", "news", "sentiment"]
    assert select_analysts(AssetType.COMMODITY, ["market", "fundamentals"]) == ["market", "macro"]


def test_routers_fall_back_on_odd_speakers() -> None:
    base = {"input": {"debate_rounds": 1, "risk_rounds": 1}}
    assert (
        should_continue_debate({**base, "invest_debate": {"count": 1, "latest_speaker": "???"}})
        == "__fallback__"
    )
    assert should_continue_debate({**base, "invest_debate": {"count": 1, "latest_speaker": "bull"}}) == "bear"
    assert (
        should_continue_debate({**base, "invest_debate": {"count": 2, "latest_speaker": "bear"}}) == "manager"
    )
    assert (
        should_continue_risk({**base, "risk_debate": {"count": 1, "latest_speaker": "robot"}})
        == "__fallback__"
    )
    assert (
        should_continue_risk({**base, "risk_debate": {"count": 2, "latest_speaker": "conservative"}})
        == "neutral"
    )
    assert should_continue_risk({**base, "risk_debate": {"count": 3, "latest_speaker": "neutral"}}) == "pm"
    for key in ("__fallback__", "bull", "bear", "manager"):
        assert key in TRADING_WORKFLOW.routers[2].path_map


async def test_happy_path_stock_waits_for_approval_then_reports(settings: Settings) -> None:
    llm = TimedFakeLLM([*happy_script(4), "## Narrative\nPTT looks constructive."])
    memory = MemoryStore()
    services = trading_services(settings, llm, memory)
    run_id = await _start(services)

    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "waiting_approval"
    events = await events_of(run_id)
    assert node_order(events) == PRE_APPROVAL
    assert not of(events, "run.finished")
    # the four analysts ran concurrently: their LLM calls overlap in time
    assert len(llm.windows) == 4
    assert max(s for s, _ in llm.windows) < min(e for _, e in llm.windows)
    # (P11) each analyst is its own UI sub-node: bracketed events, its LLM calls tagged with it
    subs = [e for e in events if e["type"] in ("node.started", "node.finished")
            and e["payload"]["node"].startswith("analyst.")]  # fmt: skip
    assert {e["payload"]["node"] for e in subs} == {
        "analyst.market", "analyst.news", "analyst.fundamentals", "analyst.sentiment"
    }  # fmt: skip
    assert all(e["payload"]["status"] == "done" for e in subs if e["type"] == "node.finished")
    assert {
        e["node"] for e in events if e["type"] == "llm.started" and (e["node"] or "").startswith("analyst.")
    } == {e["payload"]["node"] for e in subs}
    required = of(events, "approval.required")[0]
    card = required["proposal"]
    assert card["side"] == "long" and card["entry"] == 34.5
    assert card["stop"] == pytest.approx(34.5 - 2 * 0.6)
    assert card["units"] % 100 == 0 and card["units"] > 0  # SET board lot
    assert card["position_weight"] <= 0.20 + 1e-9

    await _approve(services, run_id)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "completed"
    events = await events_of(run_id)
    assert node_order(events)[-2:] == ["record_decision", "report"]
    assert run.output is not None and run.output["status"] == "approved"
    assert "No order was sent" in run.output["report_md"]
    assert "| Stop |" in run.output["report_md"]
    assert of(events, "answer.final")
    # decision log: recorded once, keyed by run
    items = await memory.search("trade_decisions", prefix="PTT.BK:")
    assert len(items) == 1 and items[0].value["run_id"] == run_id


async def test_decision_numbers_have_compute_provenance(settings: Settings) -> None:
    llm = FakeLLM([*happy_script(4), "narrative"])
    services = trading_services(settings, llm)
    run_id = await _start(services)
    required = of(await events_of(run_id), "approval.required")[0]
    assert required["provenance_ids"]
    for pid in required["provenance_ids"]:
        record = await services.provenance.get(pid)
        assert record is not None and record.kind == "compute"
    calcs = {(await services.provenance.get(p)).calculation for p in required["provenance_ids"]}  # type: ignore[union-attr]
    assert any(c and c.startswith("compute.trade.size") for c in calcs)
    assert any(c and c.startswith("compute.risk.stop_from_atr") for c in calcs)


async def test_forex_drops_fundamentals_and_uses_macro(settings: Settings) -> None:
    llm = FakeLLM([*happy_script(3), "narrative"])
    services = trading_services(settings, llm)
    run_id = await _start(services, symbol="EURUSD", analysts=["market", "news", "fundamentals"])
    events = await events_of(run_id)
    resolved = next(p for p in of(events, "node.finished") if p["node"] == "resolve_symbol")
    assert "macro" in resolved["summary"] and "fundamentals" not in resolved["summary"]
    agent_calls = [c for c in llm.calls if c.purpose == "agent"]
    systems = " ".join(c.messages[0].content for c in agent_calls)
    assert "Macro Analyst" in systems and "Fundamentals Analyst" not in systems
    card = of(events, "approval.required")[0]["proposal"]
    assert card["side"] == "long" and card["stop"] == pytest.approx(1.08 - 0.012)


async def test_research_manager_reject_skips_trader(settings: Settings) -> None:
    llm = FakeLLM([*analyst_turns(4), *debate_turns(), research_plan(Verdict.REJECT), "narrative"])
    services = trading_services(settings, llm)
    run_id = await _start(services)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "completed"
    order = node_order(await events_of(run_id))
    assert "trader" not in order and order[-1] == "report"
    assert run.output is not None and run.output["status"] == "no_trade"


async def test_pm_parse_failure_is_review_not_approve(settings: Settings) -> None:
    script = [
        *analyst_turns(4),
        *debate_turns(),
        research_plan(),
        proposal(),
        *risk_turns(),
        "not json at all",
    ]
    # default: confidence 0.0 is under the floor → no trade at all
    services = trading_services(settings, FakeLLM([*script, "narrative"]))
    run_id = await _start(services)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "rejected"
    assert run.output["verdict"] == "REVIEW"  # never auto-APPROVE

    # with the low-confidence block off, a human decides
    settings.policy_block_on_low_confidence = False
    services = trading_services(settings, FakeLLM(script))
    run_id = await _start(services)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "waiting_approval"
    card = of(await events_of(run_id), "approval.required")[0]["proposal"]
    assert card["verdict"] == "REVIEW" and card["confidence"] == 0.0


def _pm_confidence(confidence: float) -> PortfolioDecision:
    return PortfolioDecision(
        rating=Rating.BUY, decision=decision(confidence=confidence), final_size_pct=1.0, summary="go"
    )


async def test_confidence_below_the_floor_is_not_traded_and_not_retried(settings: Settings) -> None:
    script = [*analyst_turns(4), *debate_turns(), research_plan(), proposal(), *risk_turns(),
              _pm_confidence(0.55), "narrative"]  # fmt: skip
    services = trading_services(settings, FakeLLM(script))
    run_id = await _start(services)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "rejected"
    events = await events_of(run_id)
    assert not of(events, "approval.required")
    assert node_order(events).count("trader") == 1  # a retry cannot fix the PM's own confidence
    assert "min_confidence" in run.output["report_md"]


async def test_confidence_floor_is_70_and_warn_mode_keeps_the_human(settings: Settings) -> None:
    script = [*analyst_turns(4), *debate_turns(), research_plan(), proposal(), *risk_turns(),
              _pm_confidence(0.7)]  # fmt: skip
    run_id = await _start(trading_services(settings, FakeLLM(script)))
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "waiting_approval"  # 0.70 meets the floor

    settings.policy_block_on_low_confidence = False
    script[-1] = _pm_confidence(0.55)
    run_id = await _start(trading_services(settings, FakeLLM(script)))
    required = of(await events_of(run_id), "approval.required")[0]
    rule = next(r for r in required["policy_results"] if r["rule_id"] == "min_confidence")
    assert rule["passed"] is False and rule["severity"] == "warn"


async def test_rr_at_the_first_target_must_exceed_the_minimum(settings: Settings) -> None:
    # long 34.5, stop 33.3 (risk 1.2): TP1 35.0 → R:R 0.42 blocks; the retry's TP1 37.0 → 2.08 passes
    script = [*analyst_turns(4), *debate_turns(), research_plan(), proposal(targets=[35.0, 39.0]),
              *risk_turns(), pm(), proposal(), *risk_turns(), pm()]  # fmt: skip
    llm = FakeLLM(script)
    run_id = await _start(trading_services(settings, llm))
    events = await events_of(run_id)
    gates = [p for p in of(events, "node.finished") if p["node"] == "policy_gate"]
    assert gates[0]["summary"] == "blocked: min_risk_reward" and gates[1]["summary"].startswith("passed")
    trader_calls = [c for c in llm.calls if "Return your trade proposal" in c.messages[-1].content]
    assert "R:R at the FIRST target must exceed 1" in trader_calls[0].messages[-1].content
    assert "min_risk_reward: R:R 0.42" in trader_calls[1].messages[-1].content


async def test_rr_rule_off_at_zero(settings: Settings) -> None:
    settings.policy_min_risk_reward = 0
    script = [*analyst_turns(4), *debate_turns(), research_plan(), proposal(targets=[35.0]),
              *risk_turns(), pm()]  # fmt: skip
    llm = FakeLLM(script)
    run_id = await _start(trading_services(settings, llm))
    required = of(await events_of(run_id), "approval.required")[0]
    assert "min_risk_reward" not in {r["rule_id"] for r in required["policy_results"]}
    trader_call = next(c for c in llm.calls if "Return your trade proposal" in c.messages[-1].content)
    assert "FIRST target" not in trader_call.messages[-1].content


async def test_policy_block_retries_trader_once_then_passes(settings: Settings) -> None:
    bad = proposal(basis="level", stop_level=40.0)  # stop above entry for a long → no stop
    script = [
        *analyst_turns(4), *debate_turns(), research_plan(), bad, *risk_turns(), pm(),
        proposal(), *risk_turns(), pm(),
    ]  # fmt: skip
    services = trading_services(settings, FakeLLM(script))
    run_id = await _start(services)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "waiting_approval"
    events = await events_of(run_id)
    assert node_order(events).count("trader") == 2
    gates = [p for p in of(events, "node.finished") if p["node"] == "policy_gate"]
    assert gates[0]["summary"].startswith("blocked") and "require_stop_loss" in gates[0]["summary"]
    assert gates[1]["summary"].startswith("passed")


async def test_policy_block_twice_rejects_without_approval(settings: Settings) -> None:
    bad = proposal(basis="level", stop_level=40.0)
    script = [
        *analyst_turns(4), *debate_turns(), research_plan(), bad, *risk_turns(), pm(),
        bad, *risk_turns(), pm(), "narrative",
    ]  # fmt: skip
    services = trading_services(settings, FakeLLM(script))
    run_id = await _start(services)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "completed"
    events = await events_of(run_id)
    assert not of(events, "approval.required")
    assert run.output is not None and run.output["status"] == "rejected"


async def test_llm_size_is_overwritten_by_compute(settings: Settings) -> None:
    script = [*analyst_turns(4), *debate_turns(), research_plan(), proposal(proposed_units=999_999),
              *risk_turns(), pm()]  # fmt: skip
    services = trading_services(settings, FakeLLM(script))
    run_id = await _start(services)
    events = await events_of(run_id)
    codes = [p["code"] for p in of(events, "warning")]
    assert "llm_size_overwritten" in codes
    assert of(events, "approval.required")[0]["proposal"]["units"] != 999_999


async def test_pm_half_size_is_recomputed(settings: Settings) -> None:
    aapl = proposal(targets=[220.0])  # AAPL at 200: targets beyond entry so R:R is known
    base = [*analyst_turns(4), *debate_turns(), research_plan(), aapl, *risk_turns()]
    full = FakeLLM([*base, pm()])
    half = FakeLLM([*base, pm(fraction=0.5)])
    s1, s2 = trading_services(settings, full), trading_services(settings, half)
    r1 = await _start(s1, symbol="AAPL")
    r2 = await _start(s2, symbol="AAPL", horizon="position")  # different graph signature → own thread
    u1 = of(await events_of(r1), "approval.required")[0]["proposal"]["units"]
    u2 = of(await events_of(r2), "approval.required")[0]["proposal"]["units"]
    assert u2 < u1


async def test_rejected_by_reviewer_skips_record(settings: Settings) -> None:
    memory = MemoryStore()
    services = trading_services(settings, FakeLLM([*happy_script(4), "narrative"]), memory)
    run_id = await _start(services)
    await _approve(services, run_id, "rejected", comment="not now")
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "completed" and run.output is not None
    assert run.output["status"] == "rejected"
    assert "record_decision" not in node_order(await events_of(run_id))
    assert await memory.search("trade_decisions") == []


async def test_edited_size_is_recomputed_and_rechecked(settings: Settings) -> None:
    services = trading_services(settings, FakeLLM([*happy_script(4), "narrative"]))
    run_id = await _start(services)
    before = of(await events_of(run_id), "approval.required")[0]["proposal"]["units"]
    await _approve(services, run_id, "edited", edits={"final_size_pct": 0.5})
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "approved"
    after = run.output["state"]["approval"]["sizing"]["units"]
    assert 0 < after < before


async def test_edited_stop_and_targets_are_recomputed(settings: Settings) -> None:
    """(P11) stop/targets edits are a basis: compute re-derives R:R and size, policy re-checks."""
    services = trading_services(settings, FakeLLM([*happy_script(4), "narrative"]))
    run_id = await _start(services)
    await _approve(services, run_id, "edited", edits={"stop_level": 33.0, "target_levels": [38.0]})
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "approved"
    appr = run.output["state"]["approval"]
    assert appr["sizing"]["stop"] == 33.0 and appr["sizing"]["targets"] == [38.0]
    assert appr["sizing"]["risk_reward"] == [pytest.approx((38.0 - 34.5) / (34.5 - 33.0), rel=1e-3)]
    assert appr["policy"]["blocked"] is False


@pytest.mark.parametrize(
    ("edits", "comment"),
    [
        ({"stop_level": 36.0}, "edit blocked by policy: require_stop_loss"),  # stop above a long entry
        ({"target_levels": "38"}, "invalid edit"),
        ({"stop_level": "abc"}, "invalid edit"),
    ],
)
async def test_bad_edits_are_rejected(edits: dict[str, Any], comment: str, settings: Settings) -> None:
    services = trading_services(settings, FakeLLM([*happy_script(4), "narrative"]))
    run_id = await _start(services)
    await _approve(services, run_id, "edited", edits=edits)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "rejected"
    assert run.output["state"]["approval"]["comment"].startswith(comment)


async def test_plan_is_repriced_at_the_live_quote_before_approval(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(QUOTES, "PTT.BK", 35.0)  # moved from the 34.5 snapshot close during the run
    services = trading_services(settings, FakeLLM([*happy_script(4), "narrative"]))
    run_id = await _start(services)
    card = of(await events_of(run_id), "approval.required")[0]["proposal"]
    assert card["price_source"] == "live" and card["snapshot_close"] == 34.5
    assert card["entry"] == 35.0
    assert card["stop"] == pytest.approx(35.0 - 2 * 0.6)  # the ATR stop moved with the entry
    await _approve(services, run_id)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "approved"
    assert run.output["state"]["approval"]["sizing"]["entry"] == 35.0
    assert "live 2026-09-28" in run.output["report_md"]


async def test_live_price_through_a_stop_level_blocks_before_approval(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(QUOTES, "PTT.BK", 32.9)  # fell below the trader's 33.0 stop level
    script = [*analyst_turns(4), *debate_turns(), research_plan(),
              proposal(basis="level", stop_level=33.0), *risk_turns(), pm(), "narrative"]  # fmt: skip
    services = trading_services(settings, FakeLLM(script))
    run_id = await _start(services)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "completed" and run.output is not None
    assert run.output["status"] == "rejected"
    assert "approval" not in node_order(await events_of(run_id))
    assert "live price 32.9" in run.output["report_md"]


async def test_price_moving_after_the_card_rejects_the_approval(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = MemoryStore()
    monkeypatch.setitem(QUOTES, "PTT.BK", [34.5, 35.0])  # +0.5 > 0.5 ATR (0.3) by the time of approval
    services = trading_services(settings, FakeLLM([*happy_script(4), "narrative"]), memory)
    run_id = await _start(services)
    await _approve(services, run_id)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "rejected"
    appr = run.output["state"]["approval"]
    assert appr["comment"].startswith("price moved 34.5 → 35") and appr["entry_drift_atr"] > 0.5
    assert await memory.search("trade_decisions") == []


async def test_small_move_at_approval_records_the_approval_price(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(QUOTES, "PTT.BK", [34.5, 34.6])
    memory = MemoryStore()
    services = trading_services(settings, FakeLLM([*happy_script(4), "narrative"]), memory)
    run_id = await _start(services)
    await _approve(services, run_id)
    [item] = await memory.search("trade_decisions")
    assert item.value["card"]["entry"] == 34.6 and item.value["card"]["price_source"] == "live"


async def test_backtest_and_missing_quote_keep_the_snapshot_price(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(QUOTES, "PTT.BK", 99.0)  # must never be used for a past analysis date
    services = trading_services(settings, FakeLLM([*happy_script(4), "narrative"]))
    run_id = await _start(services, analysis_date="2026-09-25")
    card = of(await events_of(run_id), "approval.required")[0]["proposal"]
    assert card["price_source"] == "snapshot" and card["entry"] == 34.5

    monkeypatch.setitem(QUOTES, "PTT.BK", None)  # live run, quote provider down
    services = trading_services(settings, FakeLLM([*happy_script(4), "narrative"]))
    run_id = await _start(services)
    events = await events_of(run_id)
    assert of(events, "approval.required")[0]["proposal"]["entry"] == 34.5
    assert any(w["code"] == "live_price_unavailable" for w in of(events, "warning"))


async def test_crash_at_trader_resumes_without_rerunning_analysts(settings: Settings) -> None:
    script = [*analyst_turns(4), *debate_turns(), research_plan(), RuntimeError("trader LLM down")]
    llm = FakeLLM(script)
    services = trading_services(settings, llm)

    # make the trader's structured call raise instead of failing safe, to simulate a crash
    original = llm.structured

    async def crashing(messages: Any, schema: Any, **kw: Any) -> Any:
        if schema.__name__ == "TraderProposal" and not getattr(crashing, "done", False):
            crashing.done = True  # type: ignore[attr-defined]
            raise RuntimeError("process died in trader")
        return await original(messages, schema, **kw)

    llm.structured = crashing  # type: ignore[method-assign]
    run_id = await _start(services)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "failed"
    agent_calls_before = sum(1 for c in llm.calls if c.purpose == "agent")

    llm.script.clear()
    llm.push(proposal(), *risk_turns(), pm())
    await services.runs.resume(run_id)
    await services.runs.wait(run_id)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "waiting_approval"
    assert sum(1 for c in llm.calls if c.purpose == "agent") == agent_calls_before  # analysts not re-run


async def test_invalid_symbol_goes_straight_to_report(settings: Settings) -> None:
    services = trading_services(settings, FakeLLM([]))
    run_id = await _start(services, symbol="../../etc")
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "completed" and run.output is not None
    assert run.output["status"] == "invalid"
    assert node_order(await events_of(run_id)) == ["resolve_symbol", "report"]


# -- pending orders (limit / stop) ----------------------------------------------------------------


def _pending_script(first: Any, *rest: Any) -> list[Any]:
    return [*analyst_turns(4), *debate_turns(), research_plan(), first, *risk_turns(), pm(), *rest]


async def test_buy_limit_is_sized_from_its_price_and_survives_a_move_toward_it(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PTT 34.5, ATR 0.6: buy limit 34.0 (0.83 ATR below), ATR stop 32.8, TP1 37 → R:R 2.5
    monkeypatch.setitem(QUOTES, "PTT.BK", [34.5, 34.9])  # a market order would fail the 0.5 ATR drift
    memory = MemoryStore()
    limit = proposal(order_type="limit", order_price=34.0)
    services = trading_services(settings, FakeLLM([*_pending_script(limit), "narrative"]), memory)
    run_id = await _start(services)
    card = of(await events_of(run_id), "approval.required")[0]["proposal"]
    assert (card["order"], card["entry"], card["market_price"]) == ("buy_limit", 34.0, 34.5)
    assert card["stop"] == pytest.approx(32.8) and card["risk_reward"][0] == pytest.approx(2.5)
    assert card["time_stop_from"] == "fill" and card["time_stop_at"] is None and card["order_expires_at"]
    await _approve(services, run_id)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "approved"
    rec = (await memory.search("trade_decisions"))[0].value["card"]
    assert (rec["order"], rec["entry"], rec["market_price"]) == ("buy_limit", 34.0, 34.9)
    assert "BUY LIMIT 34" in run.output["report_md"]


async def test_limit_on_the_wrong_side_is_blocked_then_retried(settings: Settings) -> None:
    wrong = proposal(order_type="limit", order_price=35.0)  # a buy limit above the price
    llm = FakeLLM(_pending_script(wrong, proposal(), *risk_turns(), pm()))
    run_id = await _start(trading_services(settings, llm))
    gates = [p for p in of(await events_of(run_id), "node.finished") if p["node"] == "policy_gate"]
    assert gates[0]["summary"] == "blocked: valid_pending_order" and gates[1]["summary"].startswith("passed")
    trader_calls = [c for c in llm.calls if "Return your trade proposal" in c.messages[-1].content]
    assert "must be below the current price" in trader_calls[1].messages[-1].content
    assert "within 2 x ATR" in trader_calls[0].messages[-1].content


async def test_price_crossing_the_limit_before_approval_rejects(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(QUOTES, "PTT.BK", 33.9)  # already below the 34.0 buy limit at refresh
    limit = proposal(order_type="limit", order_price=34.0)
    run_id = await _start(trading_services(settings, FakeLLM([*_pending_script(limit), "narrative"])))
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "rejected"
    assert "valid_pending_order" in run.output["report_md"]


async def test_edit_order_price_only_on_a_pending_order(settings: Settings) -> None:
    limit = proposal(order_type="limit", order_price=34.0)
    services = trading_services(settings, FakeLLM([*_pending_script(limit), "narrative"]))
    run_id = await _start(services)
    await _approve(services, run_id, "edited", edits={"order_price": 34.2})
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "approved"
    assert run.output["state"]["approval"]["sizing"]["entry"] == 34.2

    services = trading_services(settings, FakeLLM([*happy_script(4), "narrative"]))
    run_id = await _start(services)
    await _approve(services, run_id, "edited", edits={"order_price": 34.2})  # a market order
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "rejected"
    assert "order_price can only be edited" in run.output["state"]["approval"]["comment"]
