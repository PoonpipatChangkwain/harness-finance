"""Research sessions + messages (contracts.md §11, P07 §7.7).

``POST /sessions/{id}/messages`` stores the user message and starts a
``research`` run whose input carries the session's earlier user/assistant
messages as history, so the conversation continues across turns.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.harness.llm.config import PROVIDERS, canonical_provider, is_provider_configured
from app.harness.runtime.services import get_services
from app.harness.storage import portfolio_repo, runs_repo, sessions_repo

router = APIRouter(tags=["sessions"])

_SESSION_ID_RE = re.compile(r"^ses_[0-9A-HJKMNP-TV-Z]{26}$")
HISTORY_MESSAGES = 10

SessionKind = Literal["research", "portfolio"]


class CreateSession(BaseModel):
    kind: SessionKind = "research"
    title: str | None = Field(default=None, max_length=300)
    # (P12) a portfolio chat's books; empty = every saved book
    portfolio_ids: list[str] = Field(default_factory=list, max_length=20)


class LLMChoice(BaseModel):
    """A model picked for this message (both tiers, no failover); None = the configured default."""

    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=200)


class PostMessage(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    skill: str | None = None
    llm: LLMChoice | None = None
    # (P12 §12.7) "use portfolio data": the books whose snapshot goes into this turn; empty = none
    # (a portfolio session falls back to its own books)
    portfolio_ids: list[str] = Field(default_factory=list, max_length=20)


class SessionOut(BaseModel):
    id: str
    kind: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class RunBrief(BaseModel):
    id: str
    status: str
    workflow: str
    started_at: datetime
    finished_at: datetime | None
    usage: dict[str, Any]
    input: dict[str, Any]
    active: bool = False


class SessionListItem(SessionOut):
    last_run: RunBrief | None = None


class SessionList(BaseModel):
    sessions: list[SessionListItem]


class MessageOut(BaseModel):
    id: str
    run_id: str | None
    role: str
    content: str
    created_at: datetime
    meta: dict[str, Any]


class SessionDetail(BaseModel):
    session: SessionOut
    messages: list[MessageOut]
    runs: list[RunBrief]


class MessageAccepted(BaseModel):
    run_id: str
    message_id: str


def _run_brief(run: runs_repo.RunRecord) -> RunBrief:
    return RunBrief(
        id=run.id,
        status=run.status,
        workflow=run.workflow,
        started_at=run.started_at,
        finished_at=run.finished_at,
        usage=run.usage,
        input={k: v for k, v in run.input.items() if k != "history"},
        active=get_services().runs.is_running(run.id),
    )


async def _require_session(session_id: str) -> sessions_repo.SessionRecord:
    session = await sessions_repo.get_session(session_id) if _SESSION_ID_RE.match(session_id) else None
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return session


async def _check_portfolios(ids: list[str]) -> list[str]:
    """Unique ids of existing books, in order; 400 naming any that do not exist."""
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return []
    known = {p.id for p in await portfolio_repo.list_portfolios()}
    missing = [i for i in wanted if i not in known]
    if missing:
        raise HTTPException(status_code=400, detail=f"unknown portfolio(s): {', '.join(missing)}")
    return wanted


@router.post("/sessions", response_model=SessionOut)
async def create_session(body: CreateSession) -> SessionOut:
    meta = None
    if body.kind == "portfolio":
        meta = {"portfolio_ids": await _check_portfolios(body.portfolio_ids)}
    record = await sessions_repo.create_session(body.kind, body.title, meta=meta)
    return SessionOut.model_validate(record.model_dump())


@router.get("/sessions", response_model=SessionList)
async def list_sessions(
    kind: SessionKind | None = None, limit: int = Query(default=50, ge=1, le=200)
) -> SessionList:
    items = []
    for s in await sessions_repo.list_sessions(kind, limit):
        runs = await runs_repo.list_runs(session_id=s.id, limit=1)
        items.append(
            SessionListItem(**s.model_dump(exclude={"meta"}), last_run=_run_brief(runs[0]) if runs else None)
        )
    return SessionList(sessions=items)


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str) -> SessionDetail:
    session = await _require_session(session_id)
    messages = await sessions_repo.list_messages(session_id)
    runs = sorted(await runs_repo.list_runs(session_id=session_id, limit=500), key=lambda r: r.started_at)
    return SessionDetail(
        session=SessionOut.model_validate(session.model_dump()),
        messages=[MessageOut.model_validate(m.model_dump()) for m in messages],
        runs=[_run_brief(r) for r in runs],
    )


class SessionDeleted(BaseModel):
    deleted: str
    runs: int


@router.delete("/sessions/{session_id}", response_model=SessionDeleted)
async def delete_session(session_id: str) -> SessionDeleted:
    """Delete a session and everything recorded for its runs (refused while a run is live)."""
    await _require_session(session_id)
    services = get_services()
    for run in await runs_repo.list_runs(session_id=session_id, limit=500):
        if services.runs.is_running(run.id):
            raise HTTPException(status_code=409, detail=f"run {run.id} is still running; stop it first")
    run_ids = await sessions_repo.delete_session(session_id)
    if run_ids is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    for run_id in run_ids:
        (services.settings.scratchpad_dir / f"{run_id}.jsonl").unlink(missing_ok=True)
    return SessionDeleted(deleted=session_id, runs=len(run_ids))


def _check_llm_choice(choice: LLMChoice) -> dict[str, str]:
    """Known provider with its key set (keys re-read from .env), else 400 naming the env to set."""
    try:
        provider = canonical_provider(choice.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    info = PROVIDERS[provider]
    if not is_provider_configured(provider, get_services().llm_settings()):
        raise HTTPException(
            status_code=400,
            detail=f"ยังไม่ได้ใส่ API key ของ {info.label}: ตั้ง {info.env_hint} ใน backend/.env",
        )
    return {"provider": provider, "model": choice.model.strip()}


@router.post("/sessions/{session_id}/messages", response_model=MessageAccepted)
async def post_message(session_id: str, body: PostMessage) -> MessageAccepted:
    session = await _require_session(session_id)
    services = get_services()
    portfolio_ids = await _check_portfolios(body.portfolio_ids)
    if session.kind == "portfolio" and not portfolio_ids:
        # a portfolio chat always sees its books (the session's own, or every saved book)
        portfolio_ids = list((session.meta or {}).get("portfolio_ids") or []) or [
            p.id for p in await portfolio_repo.list_portfolios()
        ]
        if not portfolio_ids:
            raise HTTPException(status_code=400, detail="no portfolio saved yet: create one before chatting")
    if body.skill is not None:
        if services.skills.get(body.skill) is None:
            raise HTTPException(status_code=400, detail=f"unknown skill {body.skill!r}")
        if not services.skills.is_available(body.skill):
            raise HTTPException(
                status_code=400, detail=f"skill {body.skill!r} is not available (missing provider)"
            )
    llm = _check_llm_choice(body.llm) if body.llm is not None else None
    for run in await runs_repo.list_runs(session_id=session_id, limit=5):
        if services.runs.is_running(run.id):
            raise HTTPException(status_code=409, detail=f"run {run.id} is still running in this session")

    previous = await sessions_repo.list_messages(session_id)
    history = [{"role": m.role, "content": m.content} for m in previous if m.role in ("user", "assistant")][
        -HISTORY_MESSAGES:
    ]
    meta: dict[str, Any] = {"skill": body.skill}
    run_input: dict[str, Any] = {"text": body.text, "skill": body.skill, "history": history}
    if portfolio_ids:
        meta["portfolio_ids"] = portfolio_ids  # the UI's "used portfolio X" chip on this turn
        run_input["portfolio_ids"] = portfolio_ids
    if llm is not None:
        meta["llm"] = f"{llm['provider']}:{llm['model']}"
        run_input["llm"] = llm
    message = await sessions_repo.add_message(session_id, "user", body.text, meta=meta)
    run_id = await services.runs.start("research", run_input, session_id=session_id)
    await sessions_repo.set_message_run(message.id, run_id)
    return MessageAccepted(run_id=run_id, message_id=message.id)
