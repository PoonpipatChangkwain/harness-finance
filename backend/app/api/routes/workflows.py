"""GET /api/workflows/{id}/graph — nodes/edges of a workflow for the UI (contracts.md §11)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.domain.workflows.spec import GraphView
from app.harness.workflow.registry import GRAPH_WORKFLOWS

router = APIRouter(tags=["workflows"])


@router.get("/workflows/{workflow_id}/graph", response_model=GraphView)
async def workflow_graph(workflow_id: str) -> GraphView:
    spec = GRAPH_WORKFLOWS.get(workflow_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"workflow {workflow_id} not found")
    return spec.graph_view()
