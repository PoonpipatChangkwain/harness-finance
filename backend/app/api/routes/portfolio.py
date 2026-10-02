"""Portfolio endpoints (phase-12 §12.5, §12.7; contracts.md §11).

Multi-portfolio under ``/portfolios`` (CRUD, analytics, CSV import, advisor, plans,
revisions, value history, cross-portfolio summary, weekly report). The single-book
``/portfolio`` routes of the original contract are aliases of ``pf_default``.

Positions are written only here, by the user: the advisor saves *plans*; applying one
is its own call that writes a revision. A book that was never saved answers 404 —
"not provided" is never presented as an empty book.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.routes.sessions import LLMChoice, _check_llm_choice
from app.domain.models.portfolio import Portfolio, portfolio_context, portfolio_hash
from app.domain.portfolio.analytics import PortfolioAnalytics
from app.domain.portfolio.apply import apply_legs
from app.domain.portfolio.entry import EntryError, PositionInput, parse_csv, resolve_positions
from app.domain.portfolio.market import data_source, fetch_quote, fx_pair
from app.domain.portfolio.summary import MarketFilter, summarize
from app.domain.workflows.portfolio_advisor import PORTFOLIO_ADVISOR_WORKFLOW, AdviseConstraints, RiskProfile
from app.harness.graph.checkpoint import graph_signature, thread_id
from app.harness.portfolio.scheduler import last_slot, next_slot
from app.harness.runtime.services import get_services
from app.harness.storage import portfolio_repo, runs_repo
from app.harness.workflow.portfolio_advisor import SCOPE as ADVISOR_SCOPE
from app.harness.workflow.portfolio_advisor import advisor_date

router = APIRouter(tags=["portfolio"])

DEFAULT_ID = portfolio_repo.DEFAULT_PORTFOLIO_ID
DEFAULT_NAME = "พอร์ตหลัก"
_OPEN_STATUSES = ("running", "waiting_approval", "interrupted")
_CSV_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {"text/csv": {"schema": {"type": "string"}}, "text/plain": {"schema": {"type": "string"}}},
    }
}


# -- models -------------------------------------------------------------------------------------


class PortfolioOut(BaseModel):
    portfolio: Portfolio
    state: Literal["positions", "flat", "not_provided"]
    portfolio_hash: str


class PortfolioBrief(BaseModel):
    id: str
    name: str
    base_currency: str
    icon: str | None
    color: str | None
    cash: Decimal | None
    positions: int
    updated_at: datetime


class PortfolioList(BaseModel):
    portfolios: list[PortfolioBrief]


class PortfolioWrite(BaseModel):
    """Full replace of a book (PUT) or a new book (POST)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=20)
    cash: Decimal | None = Field(default=None, ge=0, description="Base currency; null = not given (≠ 0)")
    positions: list[PositionInput] = Field(default_factory=list)


class PortfolioMetaPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=20)


class AdviseRequest(BaseModel):
    goal: str | None = Field(default=None, max_length=1000)
    risk_profile: RiskProfile = "balanced"
    constraints: AdviseConstraints = Field(default_factory=AdviseConstraints)
    language: Literal["th", "en"] = "th"
    llm: LLMChoice | None = None


class AdviseStarted(BaseModel):
    run_id: str
    thread_id: str


class ReportRunRequest(BaseModel):
    portfolio_ids: list[str] | None = None


def _entry_errors(errors: list[EntryError], converted: list[dict[str, Any]] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {"type": "InvalidPositions", "message": f"{len(errors)} position error(s)"},
            "errors": [e.model_dump() for e in errors],
            "converted": converted or [],
        },
    )


def _out(portfolio: Portfolio) -> PortfolioOut:
    return PortfolioOut(
        portfolio=portfolio,
        state=portfolio_context(portfolio).state,
        portfolio_hash=portfolio_hash(portfolio),
    )


async def _require(portfolio_id: str) -> Portfolio:
    portfolio = await portfolio_repo.get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"portfolio {portfolio_id} not found")
    return portfolio


async def _write(
    portfolio_id: str, body: PortfolioWrite, *, reason: str, existing: Portfolio | None
) -> PortfolioOut | JSONResponse:
    resolved = await resolve_positions(body.positions)
    if resolved.errors:
        return _entry_errors(resolved.errors, resolved.converted)
    services = get_services()
    saved, _ = await portfolio_repo.save_portfolio(
        portfolio_id,
        name=body.name or (existing.name if existing else DEFAULT_NAME),
        base_currency=(body.base_currency or (existing.base_currency if existing else "THB")).upper(),
        cash=body.cash,
        positions=resolved.positions,
        reason=reason,
        icon=body.icon if body.icon is not None else (existing.icon if existing else None),
        color=body.color if body.color is not None else (existing.color if existing else None),
        input_records=resolved.records,
        provenance=services.provenance,
    )
    services.portfolio.invalidate(portfolio_id)
    return _out(saved)


# -- multi-portfolio ----------------------------------------------------------------------------


@router.get("/portfolios", response_model=PortfolioList)
async def list_portfolios() -> PortfolioList:
    return PortfolioList(
        portfolios=[
            PortfolioBrief(
                id=p.id,
                name=p.name,
                base_currency=p.base_currency,
                icon=p.icon,
                color=p.color,
                cash=p.cash,
                positions=len(p.positions),
                updated_at=p.updated_at,
            )
            for p in await portfolio_repo.list_portfolios()
        ]
    )


@router.post("/portfolios", response_model=PortfolioOut)
async def create_portfolio(body: PortfolioWrite) -> PortfolioOut | JSONResponse:
    if not body.name:
        raise HTTPException(status_code=400, detail="name is required")
    return await _write(portfolio_repo.new_portfolio_id(), body, reason="created", existing=None)


async def _fx_into(base: str, currency: str) -> tuple[Decimal, str | None] | None:
    if base == currency:
        return Decimal(1), None
    quote, _ = await fetch_quote(fx_pair(base, currency), data_source())
    if quote is None:
        return None
    await get_services().provenance.add_many(quote.records)
    return quote.price, quote.record.id


@router.get("/portfolios/summary")
async def portfolios_summary(
    currency: str = Query(default="THB", pattern=r"^[A-Za-z]{3}$"),
    market: MarketFilter = "ALL",
    alt_currency: str | None = Query(default=None, pattern=r"^[A-Za-z]{3}$"),
) -> dict[str, Any]:
    """Every book in one currency: by portfolio and by asset (same symbol merged), market filter."""
    services = get_services()
    books: list[tuple[dict[str, Any], PortfolioAnalytics]] = []
    for p in await portfolio_repo.list_portfolios():
        meta = {
            "id": p.id,
            "name": p.name,
            "icon": p.icon,
            "color": p.color,
            "base_currency": p.base_currency,
        }
        books.append((meta, await services.portfolio.analytics(p)))
    out: dict[str, Any] = {}
    for key, ccy in (("primary", currency.upper()), ("alt", (alt_currency or "").upper())):
        if not ccy:
            continue
        fx: dict[str, tuple[Decimal, str | None]] = {}
        for base in {a.base_currency for _, a in books}:
            rate = await _fx_into(base, ccy)
            if rate is not None:
                fx[base] = rate
        summary, records = summarize(books, currency=ccy, fx=fx, market=market)
        await services.provenance.add_many(records)
        summary["fx"] = {b: {"rate": float(r), "provenance_id": pid} for b, (r, pid) in fx.items()}
        out[key] = summary
    return {**out["primary"], "alt": out.get("alt")}


@router.get("/portfolios/reports")
async def weekly_reports(
    portfolio_id: str | None = None, limit: int = Query(default=50, ge=1, le=200)
) -> dict[str, Any]:
    services = get_services()
    now = datetime.now(UTC)
    last = await services.weekly.last_run()
    items = []
    for snap in await portfolio_repo.list_snapshots(portfolio_id, limit):
        run = await runs_repo.get_run(snap.run_id) if snap.run_id else None
        items.append({**snap.model_dump(mode="json"), "run_status": run.status if run else None})
    return {
        "schedule": {
            "cron": "Fri 03:00 Asia/Bangkok",
            "enabled": services.settings.portfolio_weekly_enabled,
            "last_run": last.isoformat() if last else None,
            "last_slot": last_slot(now).isoformat(),
            "next_run": next_slot(now).isoformat(),
        },
        "reports": items,
    }


@router.post("/portfolios/reports/run")
async def run_weekly_report(body: ReportRunRequest | None = None) -> dict[str, Any]:
    """The "run now" button: snapshot + AI report for every book (or the given ones)."""
    ids = body.portfolio_ids if body is not None else None
    results = await get_services().weekly.run_once(kind="manual", portfolio_ids=ids)
    return {"started": results}


@router.get("/portfolios/{portfolio_id}", response_model=PortfolioOut)
async def get_portfolio_by_id(portfolio_id: str) -> PortfolioOut:
    return _out(await _require(portfolio_id))


@router.put("/portfolios/{portfolio_id}", response_model=PortfolioOut)
async def put_portfolio_by_id(portfolio_id: str, body: PortfolioWrite) -> PortfolioOut | JSONResponse:
    existing = await portfolio_repo.get_portfolio(portfolio_id)
    if existing is None and portfolio_id != DEFAULT_ID:
        raise HTTPException(status_code=404, detail=f"portfolio {portfolio_id} not found (create it first)")
    return await _write(portfolio_id, body, reason="edit" if existing else "created", existing=existing)


@router.patch("/portfolios/{portfolio_id}", response_model=PortfolioOut)
async def patch_portfolio(portfolio_id: str, body: PortfolioMetaPatch) -> PortfolioOut:
    updated = await portfolio_repo.update_meta(portfolio_id, name=body.name, icon=body.icon, color=body.color)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"portfolio {portfolio_id} not found")
    return _out(updated)


@router.delete("/portfolios/{portfolio_id}")
async def delete_portfolio(portfolio_id: str) -> dict[str, str]:
    if not await portfolio_repo.delete_portfolio(portfolio_id):
        raise HTTPException(status_code=404, detail=f"portfolio {portfolio_id} not found")
    get_services().portfolio.invalidate(portfolio_id)
    return {"deleted": portfolio_id}


@router.get("/portfolios/{portfolio_id}/analytics")
async def get_analytics(portfolio_id: str, fresh: bool = False) -> dict[str, Any]:
    """Computed analytics (cached 60 s by the book's hash); every number carries a provenance id."""
    portfolio = await _require(portfolio_id)
    analytics = await get_services().portfolio.analytics(portfolio, fresh=fresh)
    return analytics.model_dump(mode="json")


@router.post("/portfolios/{portfolio_id}/import", openapi_extra=_CSV_BODY)
async def import_csv(portfolio_id: str, request: Request, commit: bool = False) -> Any:
    """CSV ``symbol,quantity,avg_cost,weight,note`` → preview (default) or ``?commit=1`` to save.

    Replaces the positions; cash, name and currency stay as they are.
    """
    existing = await portfolio_repo.get_portfolio(portfolio_id)
    if existing is None and portfolio_id != DEFAULT_ID:
        raise HTTPException(status_code=404, detail=f"portfolio {portfolio_id} not found")
    raw = await request.body()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8") from exc
    rows, parse_errors = parse_csv(text)
    resolved = await resolve_positions(rows)
    errors = [*parse_errors, *resolved.errors]
    preview = {
        "rows": len(rows),
        "positions": [p.model_dump(mode="json") for p in resolved.positions],
        "converted": resolved.converted,
        "errors": [e.model_dump() for e in errors],
        "committed": False,
    }
    if not commit:
        return preview
    if errors:
        return _entry_errors(errors, resolved.converted)
    services = get_services()
    saved, revision = await portfolio_repo.save_portfolio(
        portfolio_id,
        name=existing.name if existing else DEFAULT_NAME,
        base_currency=existing.base_currency if existing else "THB",
        cash=existing.cash if existing else None,
        positions=resolved.positions,
        reason="import",
        icon=existing.icon if existing else None,
        color=existing.color if existing else None,
        input_records=resolved.records,
        provenance=services.provenance,
    )
    services.portfolio.invalidate(portfolio_id)
    return {
        **preview,
        "committed": True,
        "revision_id": revision.id,
        "portfolio": _out(saved).model_dump(mode="json"),
    }


@router.post("/portfolios/{portfolio_id}/advise", response_model=AdviseStarted)
async def advise(portfolio_id: str, body: AdviseRequest | None = None) -> AdviseStarted:
    body = body or AdviseRequest()
    portfolio = await _require(portfolio_id)
    if not portfolio.positions:
        raise HTTPException(status_code=400, detail="portfolio has no positions to rebalance")
    run_input: dict[str, Any] = {
        **body.model_dump(mode="json", exclude={"llm"}),
        "portfolio_id": portfolio_id,
        "portfolio_hash": portfolio_hash(portfolio),
    }
    if body.llm is not None:
        run_input["llm"] = _check_llm_choice(body.llm)
    tid = thread_id(
        ADVISOR_SCOPE, advisor_date("", run_input), graph_signature(PORTFOLIO_ADVISOR_WORKFLOW, run_input)
    )
    open_runs = await runs_repo.runs_for_thread(tid, statuses=_OPEN_STATUSES)
    if open_runs:
        raise HTTPException(
            status_code=409,
            detail=f"run {open_runs[0].id} for this portfolio and settings is still {open_runs[0].status}; "
            "approve, resume or cancel it first",
        )
    run_id = await get_services().runs.start("portfolio_advisor", run_input)
    return AdviseStarted(run_id=run_id, thread_id=tid)


@router.get("/portfolios/{portfolio_id}/advisor-runs")
async def list_advisor_runs(portfolio_id: str, limit: int = Query(default=10, ge=1, le=50)) -> dict[str, Any]:
    """Recent ``portfolio_advisor`` runs of a book (newest first): the page finds a run still
    waiting for approval after a reload, and the outcome of finished ones."""
    services = get_services()
    runs = [
        r
        for r in await runs_repo.list_runs(workflow="portfolio_advisor", limit=200)
        if r.input.get("portfolio_id") == portfolio_id
    ][:limit]
    return {
        "runs": [
            {
                "run_id": r.id,
                "status": r.status,
                "active": services.runs.is_running(r.id),
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "risk_profile": r.input.get("risk_profile"),
                "outcome": (r.output or {}).get("status"),
                "plan_id": (r.output or {}).get("plan_id"),
                "report_md": (r.output or {}).get("report_md") or None,
            }
            for r in runs
        ]
    }


@router.get("/portfolios/{portfolio_id}/plans")
async def list_plans(portfolio_id: str, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    await _require(portfolio_id)
    return {
        "plans": [p.model_dump(mode="json") for p in await portfolio_repo.list_plans(portfolio_id, limit)]
    }


@router.post("/portfolios/{portfolio_id}/plans/{plan_id}/apply")
async def apply_plan(
    portfolio_id: str, plan_id: str, dry_run: bool = False, force: bool = False
) -> dict[str, Any]:
    """Apply an approved plan to the positions (writes a revision). ``dry_run`` = diff only.

    409 when the plan was already applied or the book changed since the plan was made
    (``force=1`` applies anyway).
    """
    portfolio = await _require(portfolio_id)
    plan = await portfolio_repo.get_plan(plan_id)
    if plan is None or plan.portfolio_id != portfolio_id:
        raise HTTPException(status_code=404, detail=f"plan {plan_id} not found for {portfolio_id}")
    if plan.status == "applied":
        raise HTTPException(status_code=409, detail=f"plan {plan_id} was already applied")
    planned_hash = plan.proposal.get("portfolio_hash")
    if planned_hash and planned_hash != portfolio_hash(portfolio) and not force:
        raise HTTPException(
            status_code=409,
            detail="the portfolio changed since this plan was made; re-run the advisor or force",
        )
    applied = apply_legs(
        portfolio,
        list(plan.proposal.get("legs") or []),
        inputs=[pid for pid in (portfolio.provenance_id,) if pid],
    )
    result = applied.value
    diff = {
        "positions": result["diff"],
        "cash_before": None if result["cash_before"] is None else str(result["cash_before"]),
        "cash_after": None if result["cash_after"] is None else str(result["cash_after"]),
    }
    if dry_run:
        return {"dry_run": True, "diff": diff}
    services = get_services()
    saved, revision = await portfolio_repo.save_portfolio(
        portfolio_id,
        name=portfolio.name,
        base_currency=portfolio.base_currency,
        cash=result["cash_after"],
        positions=result["positions"],
        reason="apply_plan",
        icon=portfolio.icon,
        color=portfolio.color,
        plan_id=plan_id,
        input_records=applied.records,
        provenance=services.provenance,
    )
    await portfolio_repo.mark_plan_applied(plan_id, revision.id)
    services.portfolio.invalidate(portfolio_id)
    return {
        "dry_run": False,
        "diff": diff,
        "revision_id": revision.id,
        "portfolio": _out(saved).model_dump(mode="json"),
    }


@router.get("/portfolios/{portfolio_id}/revisions")
async def list_revisions(portfolio_id: str, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    await _require(portfolio_id)
    revisions = await portfolio_repo.list_revisions(portfolio_id, limit)
    return {"revisions": [r.model_dump(mode="json") for r in revisions]}


@router.get("/portfolios/{portfolio_id}/snapshots")
async def list_snapshots(portfolio_id: str, limit: int = Query(default=52, ge=1, le=520)) -> dict[str, Any]:
    await _require(portfolio_id)
    return {
        "snapshots": [
            s.model_dump(mode="json") for s in await portfolio_repo.list_snapshots(portfolio_id, limit)
        ]
    }


# -- single-book aliases (contracts.md §11: /portfolio = pf_default) -----------------------------


@router.get("/portfolio", response_model=PortfolioOut)
async def get_default_portfolio() -> PortfolioOut:
    return await get_portfolio_by_id(DEFAULT_ID)


@router.put("/portfolio", response_model=PortfolioOut)
async def put_default_portfolio(body: PortfolioWrite) -> PortfolioOut | JSONResponse:
    return await put_portfolio_by_id(DEFAULT_ID, body)


@router.post("/portfolio/import", openapi_extra=_CSV_BODY)
async def import_default_portfolio(request: Request, commit: bool = False) -> Any:
    return await import_csv(DEFAULT_ID, request, commit)


@router.get("/portfolio/analytics")
async def get_default_analytics(fresh: bool = False) -> dict[str, Any]:
    return await get_analytics(DEFAULT_ID, fresh)


@router.post("/portfolio/advise", response_model=AdviseStarted)
async def advise_default(body: AdviseRequest | None = None) -> AdviseStarted:
    return await advise(DEFAULT_ID, body)


@router.get("/portfolio/plans")
async def list_default_plans(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    return await list_plans(DEFAULT_ID, limit)


@router.post("/portfolio/plans/{plan_id}/apply")
async def apply_default_plan(plan_id: str, dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    return await apply_plan(DEFAULT_ID, plan_id, dry_run, force)


@router.get("/portfolio/revisions")
async def list_default_revisions(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    return await list_revisions(DEFAULT_ID, limit)
