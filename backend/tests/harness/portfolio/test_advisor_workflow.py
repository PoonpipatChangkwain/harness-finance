"""``portfolio_advisor`` end to end with FakeLLM and fixture prices (phase-12 §12.3, §12.6)."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.models.decision import Verdict
from app.domain.models.portfolio import portfolio_hash
from app.domain.workflows.portfolio_advisor import PORTFOLIO_ADVISOR_WORKFLOW
from app.harness.llm.fake import FakeLLM, ScriptItem
from app.harness.runtime.services import HarnessServices
from app.harness.storage import portfolio_repo, runs_repo
from app.harness.workflow.validate import validate_spec
from app.settings import Settings
from tests.domain.portfolio.fakes import install
from tests.harness.portfolio.helpers import DEFAULT_ID, approve, portfolio_services, proposal, save_book
from tests.harness.trading.helpers import events_of, node_order, of

pytestmark = pytest.mark.usefixtures("db")

DIAGNOSES: list[ScriptItem] = ["risk: AAPL is 46% of the book", "allocation: trim AAPL, keep cash"]
PERMISSIVE = {"max_weight": 0.3, "max_turnover": 0.5}


@pytest.fixture(autouse=True)
def market(monkeypatch: pytest.MonkeyPatch) -> Any:
    return install(monkeypatch)


async def _start(services: HarnessServices, **inp: Any) -> str:
    pf = await portfolio_repo.get_portfolio(inp.get("portfolio_id", DEFAULT_ID))
    run_input = {
        "portfolio_id": DEFAULT_ID,
        "risk_profile": "balanced",
        "portfolio_hash": portfolio_hash(pf) if pf else None,
        **inp,
    }
    run_id = await services.runs.start("portfolio_advisor", run_input)
    await services.runs.wait(run_id)
    return run_id


def test_spec_is_valid() -> None:
    validate_spec(PORTFOLIO_ADVISOR_WORKFLOW)


async def test_happy_path_normalizes_weights_rounds_lots_and_saves_a_plan(settings: Settings) -> None:
    before = await save_book()
    # 0.20 + 0.20 + cash 0.67 = 1.07 → normalized with a warning
    llm = FakeLLM([*DIAGNOSES, proposal({"PTT": 0.20, "AAPL": 0.20}, 0.67)])
    services = portfolio_services(settings, llm)
    run_id = await _start(services, constraints=PERMISSIVE)

    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "waiting_approval", run
    events = await events_of(run_id)
    assert [n for n in node_order(events) if "." not in n] == [
        "load_portfolio", "compute_analytics", "diagnose", "propose_rebalance", "compute_trades",
        "policy_gate", "approval",
    ]  # fmt: skip
    # each diagnostician is its own UI sub-node inside ``diagnose``
    assert {n for n in node_order(events) if "." in n} == {"diagnose.risk", "diagnose.allocation"}
    warnings = {w["code"]: w["message"] for w in of(events, "warning")}
    assert "weights_normalized" in warnings and "107.00%" in warnings["weights_normalized"]

    card = of(events, "approval.required")[0]["proposal"]
    rows = {r["symbol"]: r for r in card["rows"]}
    assert rows["PTT.BK"]["target"] == pytest.approx(0.20 / 1.07)
    assert rows["PTT.BK"]["shares"] % 100 == 0 and rows["PTT.BK"]["shares"] == -100  # SET board lot
    assert rows["AAPL"]["shares"] == -5 and rows["AAPL"]["action"] == "sell"
    assert card["cash_after"] == pytest.approx(50_000 + 3_450 + 36_000)
    assert card["turnover"] == pytest.approx((3_450 + 36_000) / 156_500)
    assert card["metrics_after"]["top1"] < card["metrics_before"]["top1"]
    required = of(events, "approval.required")[0]
    assert all(r["passed"] for r in required["policy_results"] if r["severity"] == "block")
    # the LLM's weights are recorded as an assumption, the trades as compute
    kinds = {(await services.provenance.get(pid)).kind for pid in required["provenance_ids"]}  # type: ignore[union-attr]
    assert {"llm_assumption", "compute"} <= kinds

    await approve(services, run_id)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "completed"
    assert run.output is not None and run.output["status"] == "approved" and run.output["plan_id"]
    assert "No order was sent" in run.output["report_md"] and "| PTT.BK |" in run.output["report_md"]
    plans = await portfolio_repo.list_plans(DEFAULT_ID)
    assert [p.id for p in plans] == [run.output["plan_id"]] and plans[0].status == "approved"
    # approving saves a plan only — the positions never change here
    after = await portfolio_repo.get_portfolio(DEFAULT_ID)
    assert after is not None and portfolio_hash(after) == portfolio_hash(before)
    assert len(await portfolio_repo.list_revisions(DEFAULT_ID)) == 1


async def test_policy_block_goes_back_to_the_advisor_once(settings: Settings) -> None:
    await save_book()
    script: list[ScriptItem] = [
        *DIAGNOSES,
        proposal({"PTT": 0.22, "AAPL": 0.46}, 0.32),  # keeps AAPL at 46% > 20% cap
        proposal({"PTT": 0.15, "AAPL": 0.15}, 0.70),
    ]
    llm = FakeLLM(script)
    services = portfolio_services(settings, llm)
    run_id = await _start(services, constraints={"max_turnover": 0.5})
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "waiting_approval"
    events = await events_of(run_id)
    assert node_order(events).count("propose_rebalance") == 2
    gates = [p for p in of(events, "node.finished") if p["node"] == "policy_gate"]
    assert gates[0]["summary"].startswith("blocked") and "max_position_weight" in gates[0]["summary"]
    assert gates[1]["summary"].startswith("passed")
    retry_prompt = [c for c in llm.calls if c.schema == "RebalanceProposal"][1].messages[-1].content
    assert "blocked by the policy" in retry_prompt and "AAPL" in retry_prompt
    assert "Computed from that proposal: turnover" in retry_prompt  # the blocked plan's numbers, to aim


async def test_policy_block_twice_ends_without_approval(settings: Settings) -> None:
    await save_book()
    stuck = proposal({"PTT": 0.22, "AAPL": 0.46}, 0.32)
    services = portfolio_services(settings, FakeLLM([*DIAGNOSES, stuck, stuck]))
    run_id = await _start(services)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "completed"
    assert run.output is not None and run.output["status"] == "rejected"
    assert not of(await events_of(run_id), "approval.required")
    assert await portfolio_repo.list_plans(DEFAULT_ID) == []


async def test_unparseable_proposal_is_review_and_stops(settings: Settings) -> None:
    await save_book()
    services = portfolio_services(settings, FakeLLM([*DIAGNOSES, "I think you should buy more"]))
    run_id = await _start(services)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "no_proposal"
    events = await events_of(run_id)
    assert "compute_trades" not in node_order(events)
    assert any(w["code"] == "structured_failed" for w in of(events, "warning"))


async def test_no_rebalance_needed(settings: Settings) -> None:
    await save_book()
    keep = proposal({"PTT": 0.22, "AAPL": 0.46}, 0.32, verdict=Verdict.REJECT)
    services = portfolio_services(settings, FakeLLM([*DIAGNOSES, keep]))
    run_id = await _start(services)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "no_change"


async def test_omitted_holding_and_unknown_symbol_are_warned(settings: Settings) -> None:
    await save_book()
    # AAPL left out (→ sold to 0), a bogus ticker dropped, MSFT added without being listed as new
    # (and cash listed as a "symbol" — real models do this — is folded into the cash target)
    odd = proposal({"PTT": 0.2, "../x": 0.1, "MSFT": 0.1, "CASH": 0.3}, 0.3)
    services = portfolio_services(settings, FakeLLM([*DIAGNOSES, odd]))
    run_id = await _start(services, constraints=PERMISSIVE | {"max_turnover": 1.0})
    codes = {w["code"] for w in of(await events_of(run_id), "warning")}
    assert {"holding_omitted", "invalid_symbol", "unlisted_new_symbol", "cash_as_symbol"} <= codes
    required = of(await events_of(run_id), "approval.required")[0]["proposal"]
    assert required["cash_target"] == pytest.approx(0.6 / 0.9)
    assert "CASH" not in {r["symbol"] for r in required["rows"]}
    card = of(await events_of(run_id), "approval.required")[0]["proposal"]
    rows = {r["symbol"]: r for r in card["rows"]}
    assert rows["AAPL"]["shares"] == -10 and rows["MSFT"]["action"] == "buy"


async def test_edit_recomputes_and_rechecks(settings: Settings) -> None:
    await save_book()
    services = portfolio_services(settings, FakeLLM([*DIAGNOSES, proposal({"PTT": 0.2, "AAPL": 0.2}, 0.6)]))
    run_id = await _start(services, constraints=PERMISSIVE)
    await approve(services, run_id, "edited", edits={"target_weights": {"AAPL": 0.25}})
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "approved"
    appr = run.output["state"]["approval"]
    assert appr["outcome"] == "edited" and appr["policy"]["blocked"] is False
    assert appr["targets"]["AAPL"] == pytest.approx(0.25 / 1.05)
    plan = (await portfolio_repo.list_plans(DEFAULT_ID))[0]
    legs = {leg["symbol"]: leg for leg in plan.proposal["legs"]}
    assert legs["AAPL"]["shares"] == -4  # 46.0% → 23.8%: 34,738 THB / 7,200 per share, floored
    assert plan.proposal["targets"]["AAPL"] == pytest.approx(0.25 / 1.05)


@pytest.mark.parametrize(
    ("edits", "comment"),
    [
        ({"target_weights": {"AAPL": 0.9}}, "edit blocked by policy"),
        ({"target_weights": {"MSFT": 0.1}}, "invalid edit"),
        ({"cash_target": 0.2}, "invalid edit"),
    ],
)
async def test_bad_edits_are_rejected(settings: Settings, edits: dict[str, Any], comment: str) -> None:
    await save_book()
    services = portfolio_services(settings, FakeLLM([*DIAGNOSES, proposal({"PTT": 0.2, "AAPL": 0.2}, 0.6)]))
    run_id = await _start(services, constraints=PERMISSIVE)
    await approve(services, run_id, "edited", edits=edits)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "declined"
    assert comment in run.output["state"]["approval"]["comment"]
    assert await portfolio_repo.list_plans(DEFAULT_ID) == []


async def test_missing_empty_and_declared_books_stop_early(settings: Settings) -> None:
    services = portfolio_services(settings, FakeLLM())
    run_id = await _start(services, portfolio_id="pf_nope")
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "not_provided"

    await save_book("pf_empty", rows=[])
    run_id = await _start(services, portfolio_id="pf_empty")
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "empty"

    from decimal import Decimal

    from tests.domain.portfolio.fakes import position

    await portfolio_repo.save_portfolio(
        "pf_pct", name="pct", base_currency="THB", cash=None, reason="created",
        positions=[position("PTT", "0", declared_weight=Decimal("0.5"))],
    )  # fmt: skip
    run_id = await _start(services, portfolio_id="pf_pct")
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.output is not None and run.output["status"] == "unvalued"
