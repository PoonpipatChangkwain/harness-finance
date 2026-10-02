"""``POST /api/approvals/{approval_id}`` — resolve a pending approval and resume its run (T4,
contracts.md §11, phase-09 §9.7).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.domain.workflows.spec import ApprovalResponse
from app.harness.runtime.services import get_services
from app.harness.workflow.runner import ApprovalNotPending, answer_approval

router = APIRouter(tags=["approvals"])


@router.post("/approvals/{approval_id}")
async def resolve_approval(approval_id: str, response: ApprovalResponse) -> dict[str, Any]:
    services = get_services()
    try:
        resolved = await answer_approval(
            bus=services.bus, run_manager=services.runs, approval_id=approval_id, response=response
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found") from exc
    except ApprovalNotPending as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"approval": resolved.model_dump(mode="json")}
