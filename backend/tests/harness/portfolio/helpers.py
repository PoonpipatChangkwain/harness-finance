"""Builders for portfolio tests: services with the real portfolio tools + FakeLLM, saved books."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from app.api.main import app
from app.domain.agents.schemas import RebalanceProposal, TargetWeight
from app.domain.models.decision import Decision, Verdict
from app.domain.models.portfolio import Portfolio, Position
from app.domain.tools.base import ToolSpec
from app.domain.tools.portfolio import PORTFOLIO_TOOLS
from app.domain.workflows.spec import ApprovalResponse
from app.harness.events.bus import EventBus
from app.harness.executor.registry import ToolRegistry
from app.harness.llm.fake import FakeLLM
from app.harness.portfolio.service import PortfolioService
from app.harness.portfolio.tools import portfolio_analytics_tool
from app.harness.runtime.services import HarnessServices
from app.harness.skills.registry import SkillRegistry
from app.harness.storage import approvals_repo, portfolio_repo
from app.harness.workflow.runner import answer_approval
from app.settings import Settings
from tests.domain.portfolio.fakes import position

DEFAULT_ID = portfolio_repo.DEFAULT_PORTFOLIO_ID
# PTT 1000 @ 30 + AAPL 10 @ 150 USD + 50,000 THB → 156,500 THB at the fixture prices
BOOK = [("PTT", "1000", "30"), ("AAPL", "10", "150")]


def portfolio_services(
    settings: Settings,
    llm: FakeLLM | None = None,
    *,
    extra_tools: list[ToolSpec] | None = None,
    long_term_memory: bool = False,
) -> HarnessServices:
    # (P17) memory adds LLM calls (distill, privacy judge) to the scripted sequence: off by default
    settings.memory_enabled = long_term_memory
    service = PortfolioService()
    tools = [*PORTFOLIO_TOOLS, portfolio_analytics_tool(service), *(extra_tools or [])]
    return HarnessServices(
        settings=settings,
        bus=EventBus(),
        tools=ToolRegistry(tools, capability_available=lambda cap: True),
        skills=SkillRegistry(provider_ok=lambda provider: True),
        portfolio=service,
        llm=llm if llm is not None else FakeLLM(default="ok"),
    )


async def save_book(
    portfolio_id: str = DEFAULT_ID,
    rows: list[tuple[str, str, str | None]] | None = None,
    *,
    cash: str | None = "50000",
    name: str = "พอร์ตหลัก",
    base_currency: str = "THB",
) -> Portfolio:
    positions: list[Position] = [position(s, q, a) for s, q, a in (BOOK if rows is None else rows)]
    saved, _ = await portfolio_repo.save_portfolio(
        portfolio_id,
        name=name,
        base_currency=base_currency,
        cash=Decimal(cash) if cash is not None else None,
        positions=positions,
        reason="created",
    )
    return saved


def proposal(
    targets: dict[str, float],
    cash: float,
    *,
    verdict: Verdict = Verdict.APPROVE,
    confidence: float = 0.8,
    new: list[str] | None = None,
) -> RebalanceProposal:
    return RebalanceProposal(
        targets=[
            TargetWeight(symbol=s, target_weight=w, rationale=f"{s} rationale") for s, w in targets.items()
        ],
        new_symbols=new or [],
        cash_target=cash,
        decision=Decision(verdict=verdict, confidence=confidence, reason="because"),
        summary="trim the largest names, keep cash",
    )


async def approve(services: HarnessServices, run_id: str, outcome: str = "approved", **kw: Any) -> None:
    pending = await approvals_repo.pending_for_run(run_id)
    assert pending is not None, "run is not waiting for an approval"
    await answer_approval(
        bus=services.bus,
        run_manager=services.runs,
        approval_id=pending.id,
        response=ApprovalResponse(outcome=outcome, **kw),  # type: ignore[arg-type]
    )
    await services.runs.wait(run_id)


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=15)
