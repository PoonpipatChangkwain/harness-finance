"""HTTP endpoints for the graph engine: resume, approvals, run state (T3/T4, phase-09 §9.7).

Uses the synthetic ``fixture_approval`` workflow (``tests/harness/graph/fixtures.py``)
registered directly on a test ``HarnessServices``, since the real ``research`` workflow has
no approval node to drive through HTTP.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.api.main import app
from app.harness.events.bus import EventBus
from app.harness.executor.registry import ToolRegistry
from app.harness.graph.context import make_node_context_factory
from app.harness.runtime.context import RunContext
from app.harness.runtime.services import HarnessServices
from app.harness.workflow import registry as workflow_registry
from app.harness.workflow.runner import GraphRunner
from app.settings import Settings
from tests.harness.graph.fixtures import make_approval_workflow

pytestmark = pytest.mark.usefixtures("db")

SCOPE = "_workflow_fixture_approval"


def _entry_points() -> tuple[Any, Any]:
    spec = make_approval_workflow(min_confidence=0.6, max_retries=1)

    def runner_for(ctx: RunContext) -> GraphRunner:
        return GraphRunner(spec, checkpoints=ctx.services.checkpoints, scope=SCOPE)

    def factory(ctx: RunContext) -> Any:
        return make_node_context_factory(llm=None, tools=None, agent=None, run_id=ctx.run_id)

    async def start(ctx: RunContext) -> dict[str, Any] | None:
        return await runner_for(ctx).start(ctx, factory(ctx))

    async def resume(ctx: RunContext, resume_value: Any) -> dict[str, Any] | None:
        return await runner_for(ctx).resume(ctx, factory(ctx), resume_value=resume_value)

    return start, resume, spec


def install_fixture_workflow(settings: Settings, install_services: Any, monkeypatch: Any) -> HarnessServices:
    tools = ToolRegistry([], capability_available=lambda c: True)
    services = install_services(HarnessServices(settings=settings, bus=EventBus(), tools=tools))
    start, resume, spec = _entry_points()
    services.runs.register("fixture_approval", start, resume=resume)
    monkeypatch.setitem(workflow_registry.GRAPH_WORKFLOWS, "fixture_approval", spec)
    monkeypatch.setitem(workflow_registry.GRAPH_SCOPES, "fixture_approval", SCOPE)
    return services


async def test_approval_flow_over_http(settings: Settings, install_services: Any, monkeypatch: Any) -> None:
    services = install_fixture_workflow(settings, install_services, monkeypatch)
    run_id = await services.runs.start("fixture_approval", {"confidence": 0.9})
    await services.runs.wait(run_id)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        got = (await client.get(f"/api/runs/{run_id}")).json()
        assert got["run"]["status"] == "waiting_approval"
        assert got["resumable"] is True
        assert got["current_node"] == "approval"
        assert got["pending_approval"]["kind"] == "trade_proposal"
        approval_id = got["pending_approval"]["id"]

        state = (await client.get(f"/api/runs/{run_id}/state")).json()
        assert state["next"] == ["approval"]
        assert "decision" not in state["values"]

        # A crash-style resume must be rejected while an approval is pending.
        blocked = await client.post(f"/api/runs/{run_id}/resume")
        assert blocked.status_code == 409

        resp = await client.post(f"/api/approvals/{approval_id}", json={"outcome": "approved"})
        assert resp.status_code == 200
        assert resp.json()["approval"]["status"] == "approved"

        await services.runs.wait(run_id)
        final = (await client.get(f"/api/runs/{run_id}")).json()
        assert final["run"]["status"] == "completed"
        assert final["run"]["output"]["decision"] == "APPROVE"
        assert final["resumable"] is False

        again = await client.post(f"/api/approvals/{approval_id}", json={"outcome": "approved"})
        assert again.status_code == 409


async def test_unknown_approval_404(settings: Settings, install_services: Any, monkeypatch: Any) -> None:
    install_fixture_workflow(settings, install_services, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/approvals/apr_nope", json={"outcome": "approved"})
        assert resp.status_code == 404


async def test_resume_unknown_run_404(settings: Settings, install_services: Any, monkeypatch: Any) -> None:
    install_fixture_workflow(settings, install_services, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/runs/run_01J0000000000000000000000A/resume")
        assert resp.status_code == 404
