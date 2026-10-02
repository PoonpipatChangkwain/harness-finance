"""SequentialRunner: run a ``WorkflowSpec`` node by node, no framework (F5, P07).

Follows edges (and routers, whose result must be in ``path_map``) from the
entry node, emits ``node.started`` / ``node.finished``, checks each node only
writes the fields it declared, and applies the declared reducers. Every
event emitted while a node runs is tagged with that node via
``ScopedEmitter``. P09 swaps this for the LangGraph adapter.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.domain.ports import EventPort
from app.domain.workflows.spec import NodeContext, Reducer, WorkflowSpec

SUMMARY_KEY = "_summary"
MAX_STEPS = 100

NodeHook = Callable[[str, dict[str, Any]], Awaitable[None]]


class WorkflowError(Exception):
    pass


class ScopedEmitter:
    """``EventPort`` that fills in the current node for every event."""

    def __init__(self, base: EventPort) -> None:
        self.base = base
        self.node: str | None = None

    async def emit(
        self,
        type: str,
        payload: dict[str, Any],
        *,
        node: str | None = None,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> int:
        return await self.base.emit(
            type, payload, node=node or self.node, call_id=call_id, parent_call_id=parent_call_id
        )


def apply_update(state: dict[str, Any], update: dict[str, Any], reducers: dict[str, Reducer]) -> None:
    for key, value in update.items():
        mode = reducers.get(key, "replace")
        if mode == "append":
            items = value if isinstance(value, list) else [value]
            state[key] = [*state.get(key, []), *items]
        elif mode == "merge":
            state[key] = {**state.get(key, {}), **value}
        elif mode == "add":
            state[key] = state.get(key, 0) + value
        else:
            state[key] = value


class SequentialRunner:
    def __init__(self, spec: WorkflowSpec, *, on_node_finished: NodeHook | None = None) -> None:
        self.spec = spec
        self.on_node_finished = on_node_finished
        self._next: dict[str, str] = {}
        for src, dst in spec.edges:
            if src in self._next:
                raise WorkflowError(f"node {src!r} has two plain edges; use a router")
            self._next[src] = dst
        self._routers = {r.source: r for r in spec.routers}

    def _successor(self, node_id: str, state: dict[str, Any]) -> str | None:
        router = self._routers.get(node_id)
        if router is not None:
            key = router.fn(state)
            if key not in router.path_map:
                raise WorkflowError(f"router of {node_id!r} returned {key!r}, not in its path map")
            target = router.path_map[key]
            return None if target in ("END", "__end__") else target
        return self._next.get(node_id)

    async def run(self, input: dict[str, Any], ctx: NodeContext, emitter: ScopedEmitter) -> dict[str, Any]:
        state: dict[str, Any] = {"input": self.spec.input_model.model_validate(input).model_dump()}
        current: str | None = self.spec.entry
        steps = 0
        while current is not None:
            steps += 1
            if steps > MAX_STEPS:
                raise WorkflowError(f"workflow {self.spec.id} exceeded {MAX_STEPS} steps")
            node = self.spec.node(current)
            emitter.node = node.id
            await emitter.emit("node.started", {"node": node.id, "label": node.label, "phase": node.phase})
            start = time.monotonic()
            try:
                update = dict(await node.fn(state, ctx))
                summary = update.pop(SUMMARY_KEY, None)
                extra = set(update) - node.writes
                if extra:
                    raise WorkflowError(f"node {node.id!r} wrote undeclared fields {sorted(extra)}")
            except BaseException:
                await emitter.emit(
                    "node.finished",
                    {"node": node.id, "status": "error", "duration_ms": _ms(start)},
                )
                emitter.node = None
                raise
            apply_update(state, update, self.spec.reducers)
            await emitter.emit(
                "node.finished",
                {"node": node.id, "status": "done", "duration_ms": _ms(start), "summary": summary},
            )
            emitter.node = None
            if self.on_node_finished is not None:
                await self.on_node_finished(node.id, state)
            current = self._successor(node.id, state)
        return state


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
