"""``graph_demo``: dev-only A -> B -> C graph exercising checkpoint/resume by hand (P09 DoD).

``POST /api/dev/graph-demo`` starts it; node B sleeps for a few seconds, which is enough
time to ``kill -9`` (or Ctrl+C) the server process while it is "in" B, restart the server,
and ``POST /runs/{id}/resume`` — A is not re-run (its checkpoint already has B's result),
and only B (which never finished) runs again before C completes the run.
"""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from app.domain.workflows.spec import NodeContext, NodeKind, NodeSpec, WorkflowSpec
from app.harness.graph.context import make_node_context_factory
from app.harness.runtime.context import RunContext
from app.harness.workflow.runner import GraphRunner

DEMO_SCOPE = "_workflow_graph_demo"
B_SLEEP_S = 5.0


class DemoIn(BaseModel):
    label: str = "demo"


class DemoOut(BaseModel):
    log: list[str] = Field(default_factory=list)


class DemoState(TypedDict, total=False):
    input: dict[str, Any]
    log: list[str]


async def _node_a(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    return {"log": ["a"]}


async def _node_b(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    await asyncio.sleep(B_SLEEP_S)
    return {"log": ["b"]}


async def _node_c(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    return {"log": ["c"]}


_LOG = frozenset({"log"})

GRAPH_DEMO_WORKFLOW = WorkflowSpec(
    id="graph_demo",
    version="1",
    input_model=DemoIn,
    output_model=DemoOut,
    state_model=DemoState,
    reducers={"log": "append"},
    nodes=[
        NodeSpec(id="a", label="A", kind=NodeKind.COMPUTE, phase="retrieve", fn=_node_a, writes=_LOG),
        NodeSpec(id="b", label="B", kind=NodeKind.COMPUTE, phase="analyze", fn=_node_b, writes=_LOG),
        NodeSpec(id="c", label="C", kind=NodeKind.COMPUTE, phase="report", fn=_node_c, writes=_LOG),
    ],
    edges=[("a", "b"), ("b", "c")],
    entry="a",
)


def _factory(ctx: RunContext) -> Any:
    # A pure-compute demo: no LLM/tools/agent node ever touches these fields.
    return make_node_context_factory(llm=None, tools=None, agent=None, run_id=ctx.run_id)  # type: ignore[arg-type]


def _runner(ctx: RunContext) -> GraphRunner:
    return GraphRunner(GRAPH_DEMO_WORKFLOW, checkpoints=ctx.services.checkpoints, scope=DEMO_SCOPE)


async def graph_demo(ctx: RunContext) -> dict[str, Any] | None:
    return await _runner(ctx).start(ctx, _factory(ctx))


async def graph_demo_resume(ctx: RunContext, resume_value: Any) -> dict[str, Any] | None:
    return await _runner(ctx).resume(ctx, _factory(ctx), resume_value=resume_value)
