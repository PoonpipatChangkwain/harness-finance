"""Portfolio-advisor event stream for the frontend tests (P13 §13.5), recorded from the real harness.

With ``RECORD_FE_FIXTURES=1`` it is also written to ``frontend/tests/fixtures/``:
``advisor-run-edit.jsonl`` (SSE envelopes: diagnose → propose → plan → policy → approval
→ edited → save → report):

    RECORD_FE_FIXTURES=1 uv run pytest tests/harness/portfolio/test_frontend_fixtures.py
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.workflows.spec import ApprovalResponse
from app.harness.llm.fake import FakeLLM
from app.harness.storage import approvals_repo, portfolio_repo
from app.harness.workflow.runner import answer_approval
from app.settings import Settings
from tests.domain.portfolio.fakes import FakeMarket, install
from tests.harness.portfolio.helpers import portfolio_services, proposal, save_book
from tests.harness.research.test_frontend_fixtures import envelopes, of_type
from tests.harness.trading.test_frontend_fixtures import save

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def market(monkeypatch: pytest.MonkeyPatch) -> FakeMarket:
    return install(monkeypatch)


async def test_advisor_edit_targets_then_saved(settings: Settings, install_services: Any) -> None:
    llm = FakeLLM(["risk brief", "allocation brief", proposal({"PTT": 0.20, "AAPL": 0.20}, 0.60)])
    services = install_services(portfolio_services(settings, llm))
    await save_book()
    run_id = await services.runs.start(
        "portfolio_advisor",
        {
            "portfolio_id": "pf_default",
            "risk_profile": "balanced",
            "constraints": {"max_weight": 0.3, "max_turnover": 0.5},
        },
    )
    await services.runs.wait(run_id)
    pending = await approvals_repo.pending_for_run(run_id)
    assert pending is not None
    edits = {"target_weights": {"PTT.BK": 0.25, "AAPL": 0.25}, "cash_target": 0.5}
    await answer_approval(
        bus=services.bus,
        run_manager=services.runs,
        approval_id=pending.id,
        response=ApprovalResponse(outcome="edited", edits=edits),
    )
    await services.runs.wait(run_id)
    events = await envelopes(run_id, None)

    required = of_type(events, "approval.required")[0]["payload"]
    assert required["kind"] == "rebalance_plan" and required["proposal"]["rows"]
    assert of_type(events, "approval.resolved")[0]["payload"]["outcome"] == "edited"
    assert of_type(events, "run.finished")[0]["payload"]["status"] == "completed"
    plans = await portfolio_repo.list_plans("pf_default")
    assert len(plans) == 1 and plans[0].proposal["targets"]["PTT.BK"] == pytest.approx(0.25)
    save("advisor-run-edit.jsonl", events)
