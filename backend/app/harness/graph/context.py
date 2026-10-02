"""``NodeContext`` for graph-compiled workflows (phase-09 §9.3/§9.6).

A fresh instance is built per node execution (``compile.py`` calls the
factory inside its wrapper) so ``emit`` is bound to that node and ``tools``
is limited to ``node_spec.tools`` — concurrent nodes in the same superstep
never share mutable state. ``request_approval`` is the one place ``T4``
touches LangGraph directly: it calls ``interrupt()`` and hands the resumed
value back as an ``ApprovalResponse``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from langgraph.types import interrupt

from app.domain.ports import AgentPort, EventPort, LLMPort, ToolCallReq, ToolRunnerPort
from app.domain.workflows.spec import ApprovalRequest, ApprovalResponse, NodeSpec
from app.harness.graph.compile import NodeContextFactory


@dataclass
class RestrictedToolRunner:
    """Rejects any call whose tool name is not in ``allowed`` (``node_spec.tools``)."""

    inner: ToolRunnerPort
    allowed: frozenset[str]

    async def run_calls(self, calls: list[ToolCallReq], *, parent_call_id: str | None = None) -> list[Any]:
        denied = sorted({c.name for c in calls} - self.allowed)
        if denied:
            raise PermissionError(f"node may only call {sorted(self.allowed)}, not {denied}")
        return await self.inner.run_calls(calls, parent_call_id=parent_call_id)


@dataclass
class GraphNodeContext:
    llm: LLMPort
    tools: ToolRunnerPort
    emit: EventPort
    agent: AgentPort
    run_id: str
    config: Mapping[str, Any] = field(default_factory=dict)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        raw = interrupt(req.model_dump(mode="json"))
        return ApprovalResponse.model_validate(raw)


def make_node_context_factory(
    *,
    llm: LLMPort,
    tools: ToolRunnerPort,
    agent: AgentPort,
    run_id: str,
    config: Mapping[str, Any] | None = None,
) -> NodeContextFactory:
    base_config = config or {}

    def factory(node: NodeSpec, emit: EventPort) -> GraphNodeContext:
        scoped_tools = RestrictedToolRunner(tools, frozenset(node.tools)) if node.tools else tools
        return GraphNodeContext(
            llm=llm, tools=scoped_tools, emit=emit, agent=agent, run_id=run_id, config=base_config
        )

    return factory
