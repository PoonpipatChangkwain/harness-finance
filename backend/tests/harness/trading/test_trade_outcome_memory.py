"""(P17 §17.2) Settling a trade also distills an anonymised ReasoningBank lesson (non-fatal)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.api.main import app
from app.domain.memory.schemas import PrivacyVerdict
from app.domain.workflows.spec import ApprovalResponse
from app.domain.workflows.trade_outcome import trade_lesson_inputs
from app.harness.llm.fake import FakeLLM
from app.harness.runtime.services import HarnessServices
from app.harness.state.memory import MemoryStore
from app.harness.storage import approvals_repo
from app.harness.workflow.runner import answer_approval
from app.settings import Settings
from tests.harness.memory.helpers import lesson, lessons
from tests.harness.trading.helpers import EVENTS, SNAPSHOT_CALLS, happy_script, trading_services

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    SNAPSHOT_CALLS.clear()
    monkeypatch.setitem(EVENTS, "rows", [])


async def _approved_run(services: HarnessServices) -> str:
    run_id = await services.runs.start("trading", {"symbol": "PTT"})
    await services.runs.wait(run_id)
    pending = await approvals_repo.pending_for_run(run_id)
    assert pending is not None
    await answer_approval(bus=services.bus, run_manager=services.runs, approval_id=pending.id,
                          response=ApprovalResponse(outcome="approved"))  # fmt: skip
    await services.runs.wait(run_id)
    return run_id


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=10)


async def test_outcome_distills_a_trade_lesson(settings: Settings, install_services: Any) -> None:
    llm = FakeLLM([*happy_script(4), "narrative"])
    services = install_services(trading_services(settings, llm, MemoryStore()))
    run_id = await _approved_run(services)
    llm.push(
        "The ATR stop was too tight for the open; widen it.",  # reflection (trade_decisions, unchanged)
        lessons(lesson("Widen intraday stops around the open", kind="strategy")),
        PrivacyVerdict(identifies_person_or_holdings=False),
    )
    async with client() as c:
        resp = await c.post(f"/api/trade/runs/{run_id}/outcome", json={"outcome": "sl", "note": "gap down"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reflection"].startswith("The ATR stop") and body["outcome"]["r_multiple"] == pytest.approx(
        -1
    )
    [item] = body["distilled"]
    assert item["kind"] == "pitfall"  # -1R is a failure
    stored = await services.reasoning_bank.get(item["id"])
    assert stored is not None and stored.outcome_signal == "failure" and stored.source_run_id == run_id
    assert stored.tags["workflow"] == "trade" and stored.tags["market"] == "SET" and "horizon" in stored.tags
    distill = next(c for c in llm.calls if c.schema == "DistilledLessons")
    assert "-1.00R" in distill.messages[-1].content


async def test_outcome_survives_a_failing_distill_and_skips_trades_without_r(
    settings: Settings, install_services: Any
) -> None:
    llm = FakeLLM([*happy_script(4), "narrative"])
    services = install_services(trading_services(settings, llm, MemoryStore()))
    run_id = await _approved_run(services)
    llm.push("lesson text", RuntimeError("distill model down"))
    async with client() as c:
        resp = await c.post(f"/api/trade/runs/{run_id}/outcome", json={"outcome": "sl"})
        assert resp.status_code == 200 and resp.json()["distilled"] == []
        # cancelled → no R → nothing to distill (and no reflection call either)
        resp = await c.post(f"/api/trade/runs/{run_id}/outcome", json={"outcome": "cancelled"})
        assert resp.status_code == 200 and resp.json()["distilled"] == []


def test_trade_lesson_inputs_signal_from_r() -> None:
    card = {"symbol": "EURUSD=X", "side": "long", "horizon": "swing"}
    win = trade_lesson_inputs({"card": card, "outcome": {"r_multiple": 2.0, "label": "take-profit hit"}})
    assert win is not None and win["signal"] == "success" and win["success"] is True
    assert win["tags"] == {"workflow": "trade", "market": "FX", "horizon": "swing"}
    scratch = trade_lesson_inputs({"card": card, "outcome": {"r_multiple": 0.1}})
    assert scratch is not None and scratch["signal"] == "mixed"
    loss = trade_lesson_inputs({"card": card, "outcome": {"r_multiple": -1.0}})
    assert loss is not None and loss["signal"] == "failure" and loss["score"] == pytest.approx(0.25)
    assert trade_lesson_inputs({"card": card, "outcome": {"r_multiple": None}}) is None
