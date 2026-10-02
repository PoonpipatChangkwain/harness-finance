"""Live trading pipeline (phase-10 §10.6): real data + real LLM, PTT and EURUSD, ≤ $0.50 each.

Run explicitly: ``uv run python -m pytest -m live tests/harness/trading/test_trading_live.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.workflows.spec import ApprovalResponse
from app.harness.runtime.services import build_services
from app.harness.storage import approvals_repo, runs_repo
from app.harness.workflow.runner import answer_approval
from app.settings import Settings

pytestmark = [pytest.mark.live, pytest.mark.usefixtures("db")]


@pytest.mark.parametrize("symbol", ["PTT", "EURUSD"])
async def test_live_pipeline_within_budget(symbol: str, settings: Settings, install_services: Any) -> None:
    services = install_services(build_services(Settings(data_dir=settings.data_dir)))
    run_id = await services.runs.start("trading", {"symbol": symbol, "language": "en"})
    await services.runs.wait(run_id)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "waiting_approval", run
    pending = await approvals_repo.pending_for_run(run_id)
    assert pending is not None and pending.request["proposal"]["stop"] is not None
    await answer_approval(
        bus=services.bus,
        run_manager=services.runs,
        approval_id=pending.id,
        response=ApprovalResponse(outcome="approved"),
    )
    await services.runs.wait(run_id)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "completed" and run.output is not None
    assert run.output["report_md"]
    assert run.usage.get("cost_usd", 0) <= 0.50
