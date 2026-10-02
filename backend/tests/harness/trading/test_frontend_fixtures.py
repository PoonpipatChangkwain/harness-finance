"""Trading event streams for the frontend tests (P11 §11.4), recorded from the real harness.

With ``RECORD_FE_FIXTURES=1`` the streams are also written to ``frontend/tests/fixtures/``:
``trading-run-<name>.jsonl`` (SSE envelopes), ``trading-run-<name>.detail.json``
(``GET /api/trade/runs/{id}``) and ``trading-graph.json`` (``/workflows/trading/graph``):

    RECORD_FE_FIXTURES=1 uv run pytest tests/harness/trading/test_frontend_fixtures.py
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from app.api.main import app
from app.domain.workflows.spec import ApprovalResponse
from app.domain.workflows.trading import TRADING_WORKFLOW
from app.harness.llm.fake import FakeLLM
from app.harness.storage import approvals_repo
from app.harness.workflow.runner import answer_approval
from app.settings import Settings
from tests.harness.research.test_frontend_fixtures import FIXTURES, envelopes, of_type
from tests.harness.trading.helpers import (
    analyst_turns,
    debate_turns,
    pm,
    proposal,
    research_plan,
    risk_turns,
    trading_services,
)

pytestmark = pytest.mark.usefixtures("db")


def save(filename: str, data: Any) -> None:
    if os.environ.get("RECORD_FE_FIXTURES") != "1":
        return
    FIXTURES.mkdir(parents=True, exist_ok=True)
    if filename.endswith(".jsonl"):
        text = "\n".join(json.dumps(e, ensure_ascii=False) for e in data) + "\n"
    else:
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    (FIXTURES / filename).write_text(text, encoding="utf-8")


async def _detail(run_id: str) -> dict[str, Any]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/trade/runs/{run_id}")
        assert resp.status_code == 200, resp.text
        return dict(resp.json())


async def test_ptt_two_debate_rounds_edited_approval(settings: Settings, install_services: Any) -> None:
    script = [
        *analyst_turns(4), *debate_turns(2), research_plan(), proposal(), *risk_turns(1), pm(),
        "## Narrative\nPTT: uptrend, buy the pullback.",
    ]  # fmt: skip
    services = install_services(trading_services(settings, FakeLLM(script)))
    run_id = await services.runs.start("trading", {"symbol": "PTT", "debate_rounds": 2, "language": "en"})
    await services.runs.wait(run_id)
    pending = await approvals_repo.pending_for_run(run_id)
    assert pending is not None
    await answer_approval(
        bus=services.bus, run_manager=services.runs, approval_id=pending.id,
        response=ApprovalResponse(outcome="edited", edits={"final_size_pct": 0.5}),
    )  # fmt: skip
    await services.runs.wait(run_id)
    events = await envelopes(run_id, None)

    speakers = [e["payload"]["node"] for e in of_type(events, "node.started")]
    assert speakers.count("bull_researcher") == 2 and speakers.count("bear_researcher") == 2
    assert {s for s in speakers if s.startswith("analyst.")} == {
        "analyst.market", "analyst.news", "analyst.fundamentals", "analyst.sentiment"
    }  # fmt: skip
    assert of_type(events, "checkpoint.saved") and of_type(events, "approval.resolved")
    # the approval pause (interrupt) is not a node error
    assert all(e["payload"]["status"] != "error" for e in of_type(events, "node.finished"))
    assert of_type(events, "run.finished")[0]["payload"]["status"] == "completed"
    detail = await _detail(run_id)
    assert detail["outcome"]["status"] == "approved"
    # usage accumulates across the approval pause; the report shows the re-checked policy
    assert detail["run"]["usage"]["llm_calls"] == len(script)
    assert "position weight 14.14%" in detail["report_md"] and "19.66%" not in detail["report_md"]
    assert [h["speaker"] for h in detail["invest_debate"]["history"]] == ["bull", "bear", "bull", "bear"]
    assert detail["risk_debate"]["count"] == 3 and detail["market_snapshot"]["close"] == 34.5
    save("trading-run-ptt.jsonl", events)
    save("trading-run-ptt.detail.json", detail)
    save("trading-graph.json", TRADING_WORKFLOW.graph_view().model_dump())


async def test_eurusd_waits_for_approval(settings: Settings, install_services: Any) -> None:
    script = [*analyst_turns(3), *debate_turns(1), research_plan(), proposal(), *risk_turns(1), pm()]
    services = install_services(trading_services(settings, FakeLLM(script)))
    run_id = await services.runs.start("trading", {"symbol": "EURUSD", "language": "en"})
    await services.runs.wait(run_id)
    events = await envelopes(run_id, None)

    started = {e["payload"]["node"] for e in of_type(events, "node.started")}
    assert "analyst.macro" in started and "analyst.fundamentals" not in started
    assert of_type(events, "approval.required") and not of_type(events, "run.finished")
    save("trading-run-eurusd.jsonl", events)
    save("trading-run-eurusd.detail.json", await _detail(run_id))
