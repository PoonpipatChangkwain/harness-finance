"""Synthetic ``WorkflowSpec``\\ s for the graph engine tests (phase-09 §9.9).

- ``LINEAR_WORKFLOW``: a → b → c, no router — crash/resume and validate_spec checks.
- ``ROUTER_WORKFLOW``: a → router({go_c: c, __fallback__: c}) → c — router fallback.
- ``APPROVAL_WORKFLOW``: propose → policy → router(ok → approval, blocked → retry once
  then reject) → approval (``interrupt()``) → execute — T4's gate pattern end to end.

Call counters are plain module dicts the tests reset per case (these specs are rebuilt
fresh by each test via the factory functions below, so state never leaks between tests).
"""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, Field

from app.domain.policy import MinConfidence, PolicySet
from app.domain.workflows.spec import (
    ApprovalRequest,
    NodeContext,
    NodeKind,
    NodeSpec,
    RouterSpec,
    WorkflowSpec,
)

# -- a -> b -> c (linear) -----------------------------------------------------------------


class LinearIn(BaseModel):
    x: int = 0


class LinearOut(BaseModel):
    x: int
    log: list[str]


class LinearState(TypedDict, total=False):
    input: dict[str, Any]
    x: int
    log: list[str]


_XY_LOG = frozenset({"x", "log"})


def make_linear_workflow(calls: dict[str, int], *, fail: dict[str, bool]) -> WorkflowSpec:
    async def a(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        calls["a"] = calls.get("a", 0) + 1
        return {"x": 1, "log": ["a"]}

    async def b(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        calls["b"] = calls.get("b", 0) + 1
        return {"x": state["x"] + 1, "log": ["b"]}

    async def c(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        calls["c"] = calls.get("c", 0) + 1
        if fail.get("c"):
            raise RuntimeError("c boom")
        return {"x": state["x"] + 1, "log": ["c"]}

    return WorkflowSpec(
        id="fixture_linear",
        version="1",
        input_model=LinearIn,
        output_model=LinearOut,
        state_model=LinearState,
        reducers={"log": "append"},
        nodes=[
            NodeSpec(id="a", label="A", kind=NodeKind.COMPUTE, phase="retrieve", fn=a, writes=_XY_LOG),
            NodeSpec(id="b", label="B", kind=NodeKind.COMPUTE, phase="analyze", fn=b, writes=_XY_LOG),
            NodeSpec(id="c", label="C", kind=NodeKind.COMPUTE, phase="report", fn=c, writes=_XY_LOG),
        ],
        edges=[("a", "b"), ("b", "c")],
        entry="a",
    )


# -- a -> router -> b | c ------------------------------------------------------------------


def make_router_workflow(route_value: dict[str, str]) -> WorkflowSpec:
    async def a(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        return {"x": 1, "log": ["a"]}

    async def b(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        return {"x": state["x"] + 10, "log": ["b"]}

    async def c(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        return {"x": state["x"] + 100, "log": ["c"]}

    def route(state: dict[str, Any]) -> str:
        return route_value["value"]

    return WorkflowSpec(
        id="fixture_router",
        version="1",
        input_model=LinearIn,
        output_model=LinearOut,
        state_model=LinearState,
        reducers={"log": "append"},
        nodes=[
            NodeSpec(id="a", label="A", kind=NodeKind.COMPUTE, phase="retrieve", fn=a, writes=_XY_LOG),
            NodeSpec(id="b", label="B", kind=NodeKind.COMPUTE, phase="analyze", fn=b, writes=_XY_LOG),
            NodeSpec(id="c", label="C", kind=NodeKind.COMPUTE, phase="report", fn=c, writes=_XY_LOG),
        ],
        edges=[],
        routers=[
            RouterSpec(
                source="a",
                fn=route,
                path_map={"go_b": "b", "go_c": "c", "__fallback__": "c"},
                returns=frozenset({"go_b", "go_c"}),
            )
        ],
        entry="a",
    )


# -- write violation ----------------------------------------------------------------------


def make_bad_write_workflow() -> WorkflowSpec:
    async def a(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        return {"secret": 1}

    return WorkflowSpec(
        id="fixture_bad_write",
        version="1",
        input_model=LinearIn,
        output_model=LinearOut,
        state_model=LinearState,
        nodes=[
            NodeSpec(
                id="a", label="A", kind=NodeKind.COMPUTE, phase="retrieve", fn=a, writes=frozenset({"x"})
            )
        ],
        edges=[],
        entry="a",
    )


# -- propose -> policy -> router -> approval -> execute (T4 gate pattern) -----------------


class ProposalIn(BaseModel):
    confidence: float = 0.3


class ProposalOut(BaseModel):
    decision: str
    log: list[str] = Field(default_factory=list)


class ApprovalState(TypedDict, total=False):
    input: dict[str, Any]
    attempts: int
    proposal: dict[str, Any]
    policy: dict[str, Any]
    recheck_result: dict[str, Any]
    decision: str
    log: list[str]


def make_approval_workflow(*, min_confidence: float = 0.6, max_retries: int = 1) -> WorkflowSpec:
    policy = PolicySet([MinConfidence(min_confidence)])

    async def propose(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        attempts = state.get("attempts", 0) + 1
        confidence = state["input"]["confidence"]
        return {"attempts": attempts, "proposal": {"confidence": confidence}, "log": [f"propose#{attempts}"]}

    async def policy_node(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        evaluation = policy.evaluate(state["proposal"], {})
        results = [r.model_dump() for r in evaluation.results]
        return {
            "policy": {"blocked": evaluation.blocked, "results": results},
            "log": [f"policy:{'blocked' if evaluation.blocked else 'ok'}"],
        }

    def route_policy(state: dict[str, Any]) -> str:
        if not state["policy"]["blocked"]:
            return "ok"
        return "retry" if state["attempts"] <= max_retries else "reject"

    async def rejected(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        return {"log": ["rejected"]}

    async def approval(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        response = await ctx.request_approval(
            ApprovalRequest(
                kind="trade_proposal",
                title="Approve proposal?",
                proposal=state["proposal"],
                policy_results=state["policy"]["results"],
            )
        )
        if response.outcome == "rejected":
            return {"decision": "REJECT", "log": ["approval:rejected"]}
        if response.outcome == "edited":
            edits = response.edits or {}
            return {
                "decision": "EDITED",
                "proposal": {**state["proposal"], **edits},
                "log": ["approval:edited"],
            }
        return {"decision": "APPROVE", "log": ["approval:approved"]}

    def route_approval(state: dict[str, Any]) -> str:
        if state["decision"] == "APPROVE":
            return "execute"
        return "recheck" if state["decision"] == "EDITED" else "end"

    async def recheck(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        """An edit changes the proposal: policy runs again before it can execute (T4)."""
        evaluation = policy.evaluate(state["proposal"], {})
        return {
            "recheck_result": {"blocked": evaluation.blocked},
            "log": [f"recheck:{'blocked' if evaluation.blocked else 'ok'}"],
        }

    def route_recheck(state: dict[str, Any]) -> str:
        return "blocked" if state["recheck_result"]["blocked"] else "ok"

    async def execute(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
        return {"log": [f"executed:{state['decision']}"]}

    return WorkflowSpec(
        id="fixture_approval",
        version="1",
        input_model=ProposalIn,
        output_model=ProposalOut,
        state_model=ApprovalState,
        reducers={"log": "append", "proposal": "merge"},
        nodes=[
            NodeSpec(
                id="propose",
                label="Propose",
                kind=NodeKind.COMPUTE,
                phase="analyze",
                fn=propose,
                writes=frozenset({"attempts", "proposal", "log"}),
            ),
            NodeSpec(
                id="policy",
                label="Policy",
                kind=NodeKind.GATE,
                phase="verify",
                fn=policy_node,
                writes=frozenset({"policy", "log"}),
            ),
            NodeSpec(
                id="rejected",
                label="Rejected",
                kind=NodeKind.COMPUTE,
                phase="report",
                fn=rejected,
                writes=frozenset({"log"}),
            ),
            NodeSpec(
                id="approval",
                label="Approval",
                kind=NodeKind.APPROVAL,
                phase="verify",
                fn=approval,
                writes=frozenset({"decision", "proposal", "log"}),
            ),
            NodeSpec(
                id="recheck",
                label="Recheck",
                kind=NodeKind.GATE,
                phase="verify",
                fn=recheck,
                writes=frozenset({"recheck_result", "log"}),
            ),
            NodeSpec(
                id="execute",
                label="Execute",
                kind=NodeKind.REPORT,
                phase="report",
                fn=execute,
                writes=frozenset({"log"}),
            ),
        ],
        edges=[("propose", "policy"), ("rejected", "END"), ("execute", "END")],
        routers=[
            RouterSpec(
                source="policy",
                fn=route_policy,
                path_map={
                    "ok": "approval",
                    "retry": "propose",
                    "reject": "rejected",
                    "__fallback__": "rejected",
                },
                returns=frozenset({"ok", "retry", "reject"}),
            ),
            RouterSpec(
                source="approval",
                fn=route_approval,
                path_map={"execute": "execute", "recheck": "recheck", "end": "END", "__fallback__": "END"},
                returns=frozenset({"execute", "recheck", "end"}),
            ),
            RouterSpec(
                source="recheck",
                fn=route_recheck,
                path_map={"ok": "execute", "blocked": "rejected", "__fallback__": "rejected"},
                returns=frozenset({"ok", "blocked"}),
            ),
        ],
        entry="propose",
        signature_fields=["confidence"],
    )
