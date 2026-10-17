"""(P17) Memory page API: what the AI remembers about the user + the shared ReasoningBank.

contracts.md §11 / §13. Every endpoint is scoped by ``X-User-Id`` (``usr_default`` when absent).
Nothing is overwritten in place: an edit is a new row that supersedes the old one; ``DELETE`` is
the user's explicit "forget" (row, or the whole chain with ``cascade=1``, plus its FTS entries).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import current_user_id
from app.domain.memory.schemas import (
    MemoryCategory,
    MemoryChange,
    MemoryItemModel,
    MemorySource,
    MemoryStatus,
    RBKind,
    RBStatus,
    ReasoningItem,
    ReconcileOpKind,
    Sensitivity,
)
from app.harness.memory.bank import UseOut
from app.harness.runtime.services import get_services

router = APIRouter(tags=["memory"])

UserId = Annotated[str, Depends(current_user_id)]
StatusFilter = Literal["active", "candidate", "superseded", "retired", "rejected", "all"]


# -- models -------------------------------------------------------------------------------------


class MemoryItemOut(BaseModel):
    id: str
    category: MemoryCategory
    key: str | None
    text: str
    value_json: dict[str, Any] | None
    confidence: float
    importance: int
    source: MemorySource
    source_ref: dict[str, Any]
    status: MemoryStatus
    supersedes: str | None
    superseded_by: str | None
    valid_from: datetime
    valid_to: datetime | None
    sensitivity: Sensitivity
    created_at: datetime
    updated_at: datetime
    last_accessed_at: datetime | None
    access_count: int
    flags: list[str] = Field(default_factory=list, description='e.g. "stated_vs_revealed"')


def _out(item: MemoryItemModel) -> MemoryItemOut:
    return MemoryItemOut(**item.model_dump(exclude={"user_id"}), flags=item.flags)


class MemoryCounts(BaseModel):
    active: int = 0
    candidate: int = 0
    superseded: int = 0
    retired: int = 0
    rejected: int = 0


class MemoryList(BaseModel):
    items: list[MemoryItemOut]
    counts: MemoryCounts


class MemoryProfileOut(BaseModel):
    profile_text: str
    token_estimate: int
    items_used: list[str]
    updated_at: datetime | None


class MemoryCreate(BaseModel):
    category: MemoryCategory
    text: str = Field(min_length=3, max_length=800)
    key: str | None = Field(default=None, max_length=120)
    value_json: dict[str, Any] | None = None


class MemoryPatch(BaseModel):
    text: str | None = Field(default=None, min_length=3, max_length=800)
    value_json: dict[str, Any] | None = None


class MemoryChangeOut(BaseModel):
    op: ReconcileOpKind
    item: MemoryItemOut
    previous: MemoryItemOut | None = None
    reason: str | None = None


def _change(change: MemoryChange) -> MemoryChangeOut:
    return MemoryChangeOut(
        op=change.op,
        item=_out(change.item),
        previous=_out(change.previous) if change.previous else None,
        reason=change.reason,
    )


class MemoryChangesOut(BaseModel):
    changes: list[MemoryChangeOut]
    warnings: list[str] = Field(default_factory=list)


class MemoryHistory(BaseModel):
    items: list[MemoryItemOut]


class MemoryDeleted(BaseModel):
    deleted: list[str]


class ReasoningCounts(BaseModel):
    total: int = 0
    candidate: int = 0
    active: int = 0
    retired: int = 0


class ReasoningList(BaseModel):
    items: list[ReasoningItem]
    counts: ReasoningCounts


class ReasoningDetail(BaseModel):
    item: ReasoningItem
    uses: list[UseOut]


class ReasoningStats(BaseModel):
    total: int
    active: int
    by_kind: dict[str, int]
    by_workflow: dict[str, int]
    avg_success_rate: float | None


# -- user memory --------------------------------------------------------------------------------


async def _require(user_id: str, memory_id: str) -> MemoryItemModel:
    item = await get_services().user_memory.get(user_id, memory_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"memory {memory_id} not found")
    return item


@router.get("/memory", response_model=MemoryList)
async def list_memory(
    user_id: UserId, status: StatusFilter = "all", category: MemoryCategory | None = None
) -> MemoryList:
    store = get_services().user_memory
    items = await store.list_items(user_id, statuses=None if status == "all" else [status], category=category)
    counts = await store.counts(user_id, category=category)
    return MemoryList(items=[_out(i) for i in items], counts=MemoryCounts(**counts))


@router.get("/memory/profile", response_model=MemoryProfileOut)
async def memory_profile(user_id: UserId) -> MemoryProfileOut:
    render, updated = await get_services().memory_engine.profile(user_id)
    return MemoryProfileOut(
        profile_text=render.text,
        token_estimate=render.token_estimate,
        items_used=render.items_used,
        updated_at=updated,
    )


@router.post("/memory", response_model=MemoryChangesOut)
async def add_memory(body: MemoryCreate, user_id: UserId) -> MemoryChangesOut:
    changes, warnings = await get_services().memory_engine.add_manual(
        user_id, category=body.category, text=body.text, key=body.key, value_json=body.value_json
    )
    return MemoryChangesOut(changes=[_change(c) for c in changes], warnings=warnings)


@router.patch("/memory/{memory_id}", response_model=MemoryItemOut)
async def edit_memory(memory_id: str, body: MemoryPatch, user_id: UserId) -> MemoryItemOut:
    if body.text is None and "value_json" not in body.model_fields_set:
        raise HTTPException(status_code=422, detail="send text and/or value_json")
    item = await _require(user_id, memory_id)
    if item.status not in ("active", "candidate"):
        raise HTTPException(
            status_code=409, detail=f"memory {memory_id} is {item.status}; edit the current one"
        )
    done = await get_services().memory_engine.edit(
        user_id,
        memory_id,
        text=body.text,
        value_json=body.value_json,
        set_value="value_json" in body.model_fields_set,
    )
    if done is None:  # pragma: no cover - deleted between the check and the write
        raise HTTPException(status_code=404, detail=f"memory {memory_id} not found")
    return _out(done[0])


@router.post("/memory/{memory_id}/confirm", response_model=MemoryItemOut)
async def confirm_memory(memory_id: str, user_id: UserId) -> MemoryItemOut:
    item = await _require(user_id, memory_id)
    if item.status != "candidate":
        raise HTTPException(status_code=409, detail=f"memory {memory_id} is {item.status}, not a candidate")
    done = await get_services().user_memory.confirm(user_id, memory_id)
    if done is None:  # pragma: no cover
        raise HTTPException(status_code=404, detail=f"memory {memory_id} not found")
    return _out(done[0])


@router.post("/memory/{memory_id}/reject", response_model=MemoryItemOut)
async def reject_memory(memory_id: str, user_id: UserId) -> MemoryItemOut:
    item = await _require(user_id, memory_id)
    if item.status != "candidate":
        raise HTTPException(status_code=409, detail=f"memory {memory_id} is {item.status}, not a candidate")
    done = await get_services().user_memory.set_status(user_id, memory_id, "rejected")
    if done is None:  # pragma: no cover
        raise HTTPException(status_code=404, detail=f"memory {memory_id} not found")
    return _out(done)


@router.post("/memory/{memory_id}/retire", response_model=MemoryItemOut)
async def retire_memory(memory_id: str, user_id: UserId) -> MemoryItemOut:
    item = await _require(user_id, memory_id)
    if item.status not in ("active", "candidate"):
        raise HTTPException(status_code=409, detail=f"memory {memory_id} is already {item.status}")
    done = await get_services().user_memory.set_status(user_id, memory_id, "retired")
    if done is None:  # pragma: no cover
        raise HTTPException(status_code=404, detail=f"memory {memory_id} not found")
    return _out(done)


@router.delete("/memory/{memory_id}", response_model=MemoryDeleted)
async def forget_memory(memory_id: str, user_id: UserId, cascade: bool = False) -> MemoryDeleted:
    deleted = await get_services().user_memory.forget(user_id, memory_id, cascade=cascade)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"memory {memory_id} not found")
    return MemoryDeleted(deleted=deleted)


@router.get("/memory/{memory_id}/history", response_model=MemoryHistory)
async def memory_history(memory_id: str, user_id: UserId) -> MemoryHistory:
    items = await get_services().user_memory.history(user_id, memory_id)
    if not items:
        raise HTTPException(status_code=404, detail=f"memory {memory_id} not found")
    return MemoryHistory(items=[_out(i) for i in items])


# -- ReasoningBank ------------------------------------------------------------------------------


@router.get("/reasoning-bank", response_model=ReasoningList)
async def list_reasoning(
    workflow: Literal["research", "portfolio", "trade"] | None = None,
    kind: RBKind | None = None,
    status: RBStatus | None = None,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
) -> ReasoningList:
    bank = get_services().reasoning_bank
    items = await bank.list_items(workflow=workflow, kind=kind, status=status, q=q, limit=limit)
    return ReasoningList(items=items, counts=ReasoningCounts(**await bank.counts()))


@router.get("/reasoning-bank/stats", response_model=ReasoningStats)
async def reasoning_stats() -> ReasoningStats:
    return ReasoningStats(**await get_services().reasoning_bank.stats())


@router.get("/reasoning-bank/{item_id}", response_model=ReasoningDetail)
async def get_reasoning(item_id: str) -> ReasoningDetail:
    bank = get_services().reasoning_bank
    item = await bank.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"reasoning item {item_id} not found")
    return ReasoningDetail(item=item, uses=await bank.uses(item_id))


@router.post("/reasoning-bank/{item_id}/retire", response_model=ReasoningItem)
async def retire_reasoning(item_id: str) -> ReasoningItem:
    item = await get_services().reasoning_bank.retire(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"reasoning item {item_id} not found")
    return item
