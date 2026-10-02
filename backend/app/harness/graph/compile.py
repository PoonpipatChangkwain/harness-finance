"""Compile a framework-neutral ``WorkflowSpec`` onto LangGraph (T1/T2, phase-09 §9.3).

The domain never sees LangGraph (F2): it declares nodes/edges/routers/
reducers as plain data, and this module is the only place that turns them
into a ``StateGraph``. Every node is wrapped so it always emits
``node.started``/``node.finished`` and can only write the state fields it
declared (``StateWriteViolation`` otherwise, checked in dev and prod alike);
every router is wrapped so an unexpected return value falls back instead of
crashing the run.
"""

from __future__ import annotations

import operator
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict, get_type_hints

from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Checkpointer

from app.domain.ports import EventPort
from app.domain.workflows.spec import FALLBACK_KEY, NodeContext, NodeSpec, WorkflowSpec
from app.harness.workflow.sequential import SUMMARY_KEY, ScopedEmitter

NodeContextFactory = Callable[[NodeSpec, EventPort], NodeContext]


class StateWriteViolation(Exception):
    pass


def _merge_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {**a, **b}


_REDUCER_FNS: dict[str, Callable[[Any, Any], Any]] = {
    "append": operator.add,
    "add": operator.add,
    "merge": _merge_dicts,
}


def build_state_schema(spec: WorkflowSpec) -> type[Any]:
    """A LangGraph-ready ``TypedDict``: same fields as ``spec.state_model``, reducers
    that are not ``"replace"`` turned into ``Annotated[type, fn]`` channels."""
    hints = get_type_hints(spec.state_model, include_extras=True)
    fields: dict[str, Any] = {}
    for name, typ in hints.items():
        fn = _REDUCER_FNS.get(spec.reducers.get(name, "replace"))
        fields[name] = Annotated[typ, fn] if fn is not None else typ
    schema: type[Any] = TypedDict(f"_{spec.id}_graph_state", fields, total=False)  # type: ignore[misc]
    return schema


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _wrap_node(
    node: NodeSpec,
    *,
    emit: ScopedEmitter,
    node_context_factory: NodeContextFactory,
    thread_id: str,
) -> Callable[[dict[str, Any]], Any]:
    # ``emit.node`` is mutated around the call rather than wrapped fresh so that a
    # workflow's LLM/tool executor — bound once for the whole run, per the ``AgentPort``
    # contract (P07) — still tags its events with "the node running right now". Nodes
    # whose ``fn`` only ever calls ``node_ctx.emit`` directly are unaffected either way.
    # (Concurrent nodes in the same superstep would race on this — none of ours do yet;
    # see phase-09 report for the follow-up once P10 adds a parallel analyst fan-out.)
    async def run_node(state: dict[str, Any]) -> dict[str, Any]:
        emit.node = node.id
        await emit.emit("node.started", {"node": node.id, "label": node.label, "phase": node.phase})
        node_ctx = node_context_factory(node, emit)
        start = time.monotonic()
        try:
            update = dict(await node.fn(state, node_ctx))
            summary = update.pop(SUMMARY_KEY, None)
            extra = set(update) - node.writes
            if extra:
                raise StateWriteViolation(f"node {node.id!r} wrote undeclared fields {sorted(extra)}")
        except GraphInterrupt:
            # paused for a human (``interrupt()``), not failed: the runner emits
            # approval.required and the node starts again on resume
            emit.node = None
            raise
        except BaseException:
            await emit.emit("node.finished", {"node": node.id, "status": "error", "duration_ms": _ms(start)})
            emit.node = None
            raise
        await emit.emit(
            "node.finished",
            {"node": node.id, "status": "done", "duration_ms": _ms(start), "summary": summary},
        )
        await emit.emit("checkpoint.saved", {"node": node.id, "thread_id": thread_id}, node=node.id)
        emit.node = None
        return update

    return run_node


def _wrap_router(router: Any, *, emit: ScopedEmitter) -> Callable[[dict[str, Any]], Any]:
    async def run_router(state: dict[str, Any]) -> str:
        key = router.fn(state)
        if key not in router.path_map:
            await emit.emit(
                "warning",
                {"code": "router_fallback", "message": f"router of {router.source!r} returned {key!r}"},
                node=router.source,
            )
            key = FALLBACK_KEY
        target = router.path_map[key]
        return END if target in ("END", "__end__") else target

    return run_router


@dataclass
class CompiledWorkflow:
    spec: WorkflowSpec
    graph: Any  # langgraph CompiledStateGraph


def compile_workflow(
    spec: WorkflowSpec,
    *,
    checkpointer: Checkpointer,
    node_context_factory: NodeContextFactory,
    emit: ScopedEmitter,
    thread_id: str,
) -> CompiledWorkflow:
    schema = build_state_schema(spec)
    graph: StateGraph[Any, None, Any, Any] = StateGraph(schema)
    for node in spec.nodes:
        graph.add_node(  # type: ignore[call-overload]
            node.id,
            _wrap_node(node, emit=emit, node_context_factory=node_context_factory, thread_id=thread_id),
        )
    graph.add_edge(START, spec.entry)
    has_outgoing: set[str] = set()
    for src, dst in spec.edges:
        graph.add_edge(src, END if dst in ("END", "__end__") else dst)
        has_outgoing.add(src)
    for router in spec.routers:
        graph.add_conditional_edges(router.source, _wrap_router(router, emit=emit))
        has_outgoing.add(router.source)
    for node in spec.nodes:
        if node.id not in has_outgoing:
            graph.add_edge(node.id, END)
    compiled = graph.compile(checkpointer=checkpointer)
    return CompiledWorkflow(spec=spec, graph=compiled)
