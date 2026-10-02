"""Dev-only endpoints (APP_ENV=dev)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.harness.runtime.services import get_services

router = APIRouter(tags=["dev"])


@router.post("/dev/echo-run")
async def echo_run(symbol: str = Query(default="PTT", min_length=1, max_length=20)) -> dict[str, str]:
    services = get_services()
    if services.settings.app_env != "dev":
        raise HTTPException(status_code=404, detail="not found")
    run_id = await services.runs.start("echo_tools", {"symbol": symbol})
    return {"run_id": run_id}


@router.post("/dev/graph-demo")
async def graph_demo_run(label: str = Query(default="demo")) -> dict[str, str]:
    """A -> B -> C on LangGraph (T3 DoD): kill the server while node B is running (it
    sleeps a few seconds), restart, then ``POST /runs/{id}/resume`` — A is not re-run."""
    services = get_services()
    if services.settings.app_env != "dev":
        raise HTTPException(status_code=404, detail="not found")
    run_id = await services.runs.start("graph_demo", {"label": label})
    return {"run_id": run_id}
