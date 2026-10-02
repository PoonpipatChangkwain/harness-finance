"""Framework-neutral workflow declaration (F5, contracts.md §9).

A workflow is data: nodes (async functions returning partial state
updates), edges, routers, reducers. The harness runs it — sequentially in
P07 (``app.harness.workflow.sequential``), on LangGraph from P09 — so the
domain never imports an agent framework (F2).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.ports import AgentPort, EventPort, LLMPort, ToolRunnerPort

Reducer = Literal["replace", "append", "merge", "add"]


class NodeKind(StrEnum):
    AGENT = "agent"
    COMPUTE = "compute"
    GATE = "gate"
    APPROVAL = "approval"
    REPORT = "report"


class ApprovalRequest(BaseModel):
    kind: str
    title: str
    proposal: dict[str, Any]
    policy_results: list[dict[str, Any]] = Field(default_factory=list)
    provenance_ids: list[str] = Field(default_factory=list)


class ApprovalResponse(BaseModel):
    outcome: Literal["approved", "rejected", "edited"]
    edits: dict[str, Any] | None = None
    comment: str | None = None


class NodeContext(Protocol):
    """What a node may use; the harness implements it."""

    llm: LLMPort
    tools: ToolRunnerPort
    emit: EventPort
    agent: AgentPort  # (P07) the harness agent loop / final answer
    run_id: str
    config: Mapping[str, Any]

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse: ...


NodeFn = Callable[[dict[str, Any], NodeContext], Awaitable[dict[str, Any]]]


class NodeSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    label: str
    kind: NodeKind
    phase: str  # retrieve | analyze | verify | compute | synthesize | report
    fn: NodeFn
    writes: frozenset[str]
    tools: list[str] = Field(default_factory=list)
    idempotent: bool = True


FALLBACK_KEY = "__fallback__"


class RouterSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: str
    fn: Callable[[dict[str, Any]], str]
    path_map: dict[str, str]
    # Every value ``fn`` can return (P09 §9.2): checked against ``path_map`` at
    # register time so a router can never come back with a key nobody wired up.
    returns: frozenset[str] = Field(default_factory=frozenset)


class GraphView(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class WorkflowSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    version: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    state_model: type[Any]
    reducers: dict[str, Reducer] = Field(default_factory=dict)
    nodes: list[NodeSpec]
    edges: list[tuple[str, str]]
    routers: list[RouterSpec] = Field(default_factory=list)
    entry: str
    signature_fields: list[str] = Field(default_factory=list)

    def node(self, node_id: str) -> NodeSpec:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def graph_view(self) -> GraphView:
        nodes = [{"id": n.id, "label": n.label, "kind": n.kind.value, "phase": n.phase} for n in self.nodes]
        edges = [{"source": a, "target": b} for a, b in self.edges]
        for r in self.routers:
            edges += [{"source": r.source, "target": t, "label": k} for k, t in r.path_map.items()]
        return GraphView(nodes=nodes, edges=edges)
