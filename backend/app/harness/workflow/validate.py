"""``validate_spec``: fail-fast checks run once, at registration time (phase-09 §9.2).

Catches the mistakes that would otherwise only show up mid-run: a node that
writes a field nobody declared, a router that can strand the graph, two
nodes racing on the same state field with no reducer to merge them. None of
this needs LangGraph — it only looks at the plain-data ``WorkflowSpec``
(F2), which is why it lives in ``harness/workflow`` rather than
``harness/graph``.
"""

from __future__ import annotations

from app.domain.workflows.spec import FALLBACK_KEY, WorkflowSpec

REPLACE = "replace"


class WorkflowValidationError(Exception):
    pass


def validate_spec(spec: WorkflowSpec) -> None:
    _check_unique_ids_and_entry(spec)
    _check_edges_and_reachability(spec)
    _check_reducers(spec)
    _check_router_path_maps(spec)


def _check_unique_ids_and_entry(spec: WorkflowSpec) -> None:
    ids = [n.id for n in spec.nodes]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise WorkflowValidationError(f"workflow {spec.id!r} has duplicate node ids: {dupes}")
    if spec.entry not in ids:
        raise WorkflowValidationError(f"workflow {spec.id!r} entry {spec.entry!r} is not a node")


def _successors(spec: WorkflowSpec) -> dict[str, set[str]]:
    node_ids = {n.id for n in spec.nodes}
    out: dict[str, set[str]] = {n.id: set() for n in spec.nodes}
    for src, dst in spec.edges:
        if src not in node_ids:
            raise WorkflowValidationError(f"workflow {spec.id!r} edge source {src!r} is not a node")
        if dst not in node_ids and dst not in ("END", "__end__"):
            raise WorkflowValidationError(f"workflow {spec.id!r} edge target {dst!r} is not a node or END")
        out.setdefault(src, set()).add(dst)
    for router in spec.routers:
        if router.source not in node_ids:
            raise WorkflowValidationError(
                f"workflow {spec.id!r} router source {router.source!r} is not a node"
            )
        for key, target in router.path_map.items():
            if target not in node_ids and target not in ("END", "__end__"):
                raise WorkflowValidationError(
                    f"workflow {spec.id!r} router of {router.source!r} maps {key!r} to "
                    f"{target!r}, which is not a node or END"
                )
            out.setdefault(router.source, set()).add(target)
    return out


def _check_edges_and_reachability(spec: WorkflowSpec) -> None:
    successors = _successors(spec)
    seen = {spec.entry}
    frontier = [spec.entry]
    while frontier:
        current = frontier.pop()
        for nxt in successors.get(current, set()):
            if nxt in ("END", "__end__") or nxt in seen:
                continue
            seen.add(nxt)
            frontier.append(nxt)
    unreachable = {n.id for n in spec.nodes} - seen
    if unreachable:
        raise WorkflowValidationError(f"workflow {spec.id!r} has unreachable nodes: {sorted(unreachable)}")


def _reachable_from(successors: dict[str, set[str]], start: str) -> set[str]:
    seen: set[str] = set()
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for nxt in successors.get(current, set()):
            if nxt in ("END", "__end__") or nxt in seen:
                continue
            seen.add(nxt)
            frontier.append(nxt)
    return seen


def _mutually_exclusive_pairs(spec: WorkflowSpec) -> set[frozenset[str]]:
    """Targets of the *same* router's ``path_map`` never run in the same step —
    a conditional branch, unlike a plain fan-out edge, always picks exactly one."""
    pairs: set[frozenset[str]] = set()
    for router in spec.routers:
        targets = sorted({t for t in router.path_map.values() if t not in ("END", "__end__")})
        for i, u in enumerate(targets):
            for v in targets[i + 1 :]:
                pairs.add(frozenset((u, v)))
    return pairs


def _check_reducers(spec: WorkflowSpec) -> None:
    successors = _successors(spec)
    reach = {n.id: _reachable_from(successors, n.id) for n in spec.nodes}
    exclusive = _mutually_exclusive_pairs(spec)
    writers: dict[str, set[str]] = {}
    for node in spec.nodes:
        for field in node.writes:
            writers.setdefault(field, set()).add(node.id)
    for field, node_ids in writers.items():
        if len(node_ids) < 2 or spec.reducers.get(field, REPLACE) != REPLACE:
            continue
        ordered = sorted(node_ids)
        for i, u in enumerate(ordered):
            for v in ordered[i + 1 :]:
                # Safe without a reducer iff one always runs before the other (a plain-edge
                # chain), or they are mutually-exclusive branches of the same router.
                if v in reach[u] or u in reach[v] or frozenset((u, v)) in exclusive:
                    continue
                raise WorkflowValidationError(
                    f"workflow {spec.id!r} field {field!r} is written by both {u!r} and {v!r}, "
                    "which are not ordered by any edge/router and could run in the same step, "
                    "but has no reducer (or 'replace', which would race)"
                )


def _check_router_path_maps(spec: WorkflowSpec) -> None:
    for router in spec.routers:
        if FALLBACK_KEY not in router.path_map:
            raise WorkflowValidationError(
                f"workflow {spec.id!r} router of {router.source!r} has no {FALLBACK_KEY!r} entry"
            )
        missing = router.returns - router.path_map.keys()
        if missing:
            raise WorkflowValidationError(
                f"workflow {spec.id!r} router of {router.source!r} can return {sorted(missing)}, "
                "which is not in its path_map"
            )
