"""``get_portfolio_analytics``: the LLM's read-only view of the user's saved portfolios (P12 §12.3/§12.7).

Lives in the harness (it reads the repository). A run may only see the portfolios it
was given: the run input's ``portfolio_ids`` (the research "use portfolio data" toggle,
or a portfolio chat session). Anything else is refused — the tool never lists books
the user did not select.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domain.portfolio.analytics import PortfolioAnalytics, summary_text
from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec
from app.harness.portfolio.service import PortfolioService
from app.harness.storage import portfolio_repo, runs_repo

TOOL_NAME = "get_portfolio_analytics"


class PortfolioAnalyticsToolArgs(BaseModel):
    portfolio_id: str | None = Field(
        default=None, description="Portfolio id from the PORTFOLIO block; omit for the first selected one"
    )


async def allowed_portfolios(run_id: str) -> list[str]:
    run = await runs_repo.get_run(run_id)
    ids = (run.input.get("portfolio_ids") if run is not None else None) or []
    return [str(i) for i in ids]


_TOTALS = (
    "total_value", "cash", "invested_value", "cost_basis", "unrealized_pnl", "unrealized_pnl_pct", "day_pnl",
    "day_pnl_pct",
)  # fmt: skip
_HOLDING_FIELDS = {
    "symbol", "sector", "currency", "quantity", "status", "price", "market_value", "weight", "unrealized_pnl",
    "unrealized_pnl_pct", "day_change_pct", "risk_contribution",
}  # fmt: skip


def compact(a: PortfolioAnalytics) -> dict[str, Any]:
    """What the LLM needs, without the full correlation matrix."""
    pairs: list[dict[str, Any]] = []
    if a.risk:
        syms = list(a.risk.correlation)
        for i, s1 in enumerate(syms):
            for s2 in syms[i + 1 :]:
                pairs.append({"pair": f"{s1}/{s2}", "corr": round(a.risk.correlation[s1][s2], 3)})
        pairs.sort(key=lambda p: -abs(p["corr"]))
    risk = a.risk.model_dump(mode="json", exclude={"correlation"}) if a.risk else None
    return {
        "portfolio_id": a.portfolio_id,
        "name": a.name,
        "base_currency": a.base_currency,
        "as_of": a.as_of.isoformat() if a.as_of else None,
        "weights_declared_only": a.weights_declared_only,
        "totals": {k: getattr(a, k).model_dump() if getattr(a, k) is not None else None for k in _TOTALS},
        "holdings": [h.model_dump(mode="json", include=_HOLDING_FIELDS) for h in a.holdings],
        "concentration": a.concentration.model_dump() if a.concentration else None,
        "exposure": a.exposure,
        "exposure_provenance": a.exposure_provenance,
        "risk": risk,
        "top_correlations": pairs[:10],
        "warnings": [w.model_dump() for w in a.warnings],
    }


def portfolio_analytics_tool(service: PortfolioService) -> ToolSpec:
    async def run(args: PortfolioAnalyticsToolArgs, ctx: ToolContext) -> ToolOutput:
        allowed = await allowed_portfolios(ctx.run_id)
        if not allowed:
            raise ValueError("no portfolio was selected for this conversation (turn on 'use portfolio data')")
        pid = args.portfolio_id or allowed[0]
        if pid not in allowed:
            raise ValueError(f"portfolio {pid!r} was not selected; available: {', '.join(allowed)}")
        portfolio = await portfolio_repo.get_portfolio(pid)
        if portfolio is None:
            raise ValueError(f"portfolio {pid!r} does not exist")
        a = await service.analytics(portfolio)
        return ToolOutput(
            data={"summary": summary_text(a), **compact(a)},
            summary=f"{a.name}: {len(a.holdings)} holdings"
            + (f" · {a.total_value.value:,.0f} {a.base_currency}" if a.total_value else ""),
            records=list(a.records),
            status="partial" if a.warnings else "ok",
        )

    return ToolSpec(
        name=TOOL_NAME,
        description=(
            "Computed analytics of the user's selected portfolio: holdings, weights, value, unrealized and "
            "day P&L, concentration, sector/currency exposure, volatility, drawdown, beta, VaR/CVaR, top "
            "correlations and each holding's contribution to risk. Use it for any question about the user's "
            "own portfolio; never estimate these numbers."
        ),
        args_model=PortfolioAnalyticsToolArgs,
        category="portfolio",
        timeout_s=90,
        max_calls_per_run=2,
        limit_by="portfolio_id",
        fn=run,
    )
