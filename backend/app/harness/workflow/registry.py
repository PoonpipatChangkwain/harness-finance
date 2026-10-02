"""Registry of graph-compiled ``WorkflowSpec``\\ s, shared by the ``/workflows/{id}/graph``,
``/runs/{id}/resume`` and ``/runs/{id}/state`` endpoints (phase-09 §9.7). The portfolio
advisor (P12) registers its spec here too.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.domain.workflows.portfolio_advisor import PORTFOLIO_ADVISOR_WORKFLOW
from app.domain.workflows.research import RESEARCH_WORKFLOW
from app.domain.workflows.spec import WorkflowSpec
from app.domain.workflows.trading import TRADING_WORKFLOW
from app.harness.runtime.graph_demo import DEMO_SCOPE, GRAPH_DEMO_WORKFLOW
from app.harness.workflow.portfolio_advisor import SCOPE as ADVISOR_SCOPE
from app.harness.workflow.trading import trade_scope

GRAPH_WORKFLOWS: dict[str, WorkflowSpec] = {
    RESEARCH_WORKFLOW.id: RESEARCH_WORKFLOW,
    GRAPH_DEMO_WORKFLOW.id: GRAPH_DEMO_WORKFLOW,
    TRADING_WORKFLOW.id: TRADING_WORKFLOW,
    PORTFOLIO_ADVISOR_WORKFLOW.id: PORTFOLIO_ADVISOR_WORKFLOW,
}

# Checkpoint scope per workflow (contracts.md §12): a fixed ``_workflow_{id}`` file, or
# derived from the run input (trading: one file per symbol).
GRAPH_SCOPES: dict[str, str | Callable[[dict[str, Any]], str]] = {
    "research": "_workflow_research",
    "graph_demo": DEMO_SCOPE,
    "trading": trade_scope,
    "portfolio_advisor": ADVISOR_SCOPE,
}


def scope_for(workflow_id: str, input: dict[str, Any] | None = None) -> str:
    scope = GRAPH_SCOPES.get(workflow_id, f"_workflow_{workflow_id}")
    return scope(input or {}) if callable(scope) else scope
