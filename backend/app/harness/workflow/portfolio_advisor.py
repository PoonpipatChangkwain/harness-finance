"""Harness entry point of the ``portfolio_advisor`` workflow (P12): wire services, run the spec.

- the diagnosticians run through ``TradingAgent`` (a fresh ``AgentLoop`` per role) with
  their own UI sub-nodes ``diagnose.risk`` / ``diagnose.allocation``;
- ports for the domain: ``portfolio_loader`` (repository → Portfolio JSON) and
  ``plan_store`` (approved plans, keyed by run);
- checkpoint scope ``_workflow_portfolio_advisor``; thread = portfolio id + graph signature
  (risk profile, constraints, portfolio hash): the same book + settings resumes, a changed
  book is a different thread (T3). Checkpoints are cleared on success.
"""

from __future__ import annotations

from typing import Any

from app.domain.workflows.portfolio_advisor import DIAGNOSTICIANS, PORTFOLIO_ADVISOR_WORKFLOW, output_of
from app.harness.config.overrides import advisor_policy_config
from app.harness.executor.executor import ToolExecutor
from app.harness.graph.context import make_node_context_factory
from app.harness.runtime.context import RunContext
from app.harness.storage import portfolio_repo
from app.harness.workflow.runner import GraphRunner
from app.harness.workflow.sequential import ScopedEmitter
from app.harness.workflow.trading import TradingAgent

SCOPE = "_workflow_portfolio_advisor"
SUB_NODES = {role: (f"diagnose.{key}", f"{key} diagnosis") for key, role in DIAGNOSTICIANS.items()}


def advisor_date(run_id: str, input: dict[str, Any]) -> str:
    return str(input.get("portfolio_id") or portfolio_repo.DEFAULT_PORTFOLIO_ID)


class RepoPlanStore:
    async def save(self, record: dict[str, Any]) -> str:
        return await portfolio_repo.save_plan(record)


async def load_portfolio_json(portfolio_id: str) -> dict[str, Any] | None:
    portfolio = await portfolio_repo.get_portfolio(portfolio_id)
    return None if portfolio is None else portfolio.model_dump(mode="json")


def _runner(ctx: RunContext) -> GraphRunner:
    return GraphRunner(
        PORTFOLIO_ADVISOR_WORKFLOW,
        checkpoints=ctx.services.checkpoints,
        scope=SCOPE,
        date_fn=advisor_date,
        keep_checkpoints_on_success=False,
        recursion_limit=ctx.services.settings.graph_recursion_limit,
    )


def _build(ctx: RunContext) -> Any:
    services = ctx.services
    scoped = ScopedEmitter(ctx.emit)
    ctx.emit = scoped
    choice = ctx.input.get("llm") if isinstance(ctx.input.get("llm"), dict) else None
    base_llm = (
        services.get_llm_for_choice(choice["provider"], choice["model"]) if choice else services.get_llm()
    )
    llm = base_llm.bind(emit=scoped, on_usage=lambda usage, purpose: ctx.record_llm_usage(usage, purpose))
    executor = ToolExecutor(services.tools, ctx, provenance=services.provenance)
    agent = TradingAgent(
        ctx,
        llm,
        executor,
        deep_model=base_llm.primary("deep")[1],
        quick_model=base_llm.primary("quick")[1],
        deep_provider=base_llm.primary("deep")[0],
        sub_nodes=SUB_NODES,
    )
    return make_node_context_factory(
        llm=llm,
        tools=executor,
        agent=agent,
        run_id=ctx.run_id,
        config={
            "portfolio_loader": load_portfolio_json,
            "plan_store": RepoPlanStore(),
            # (P17) MemoryPort for recall_memory / learn; None = memory off
            "memory": services.memory_engine if services.settings.memory_enabled else None,
            **advisor_policy_config(services.settings),  # (P13) Settings-page policy limits
        },
    )


STATE_KEYS = (
    "portfolio", "analytics", "diagnoses", "proposal", "plan", "policy", "approval", "saved_plan", "outcome",
    "memory", "learned",
)  # fmt: skip


def _finish(state: dict[str, Any]) -> dict[str, Any]:
    """Keep the final state in the run output: the checkpoint is cleared on success (T3)."""
    state_out = {k: state.get(k) for k in STATE_KEYS}
    book = state_out.get("portfolio")
    if isinstance(book, dict):  # the book itself lives in the repository
        state_out["portfolio"] = {k: v for k, v in book.items() if k != "data"}
    return {**output_of(state).model_dump(), "state": state_out}


async def advisor_workflow(ctx: RunContext) -> dict[str, Any] | None:
    state = await _runner(ctx).start(ctx, _build(ctx))
    return None if state is None else _finish(state)


async def advisor_workflow_resume(ctx: RunContext, resume_value: Any) -> dict[str, Any] | None:
    state = await _runner(ctx).resume(ctx, _build(ctx), resume_value=resume_value)
    return None if state is None else _finish(state)
