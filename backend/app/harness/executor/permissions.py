"""Permission policy: allow / ask / deny per tool call (D3, T4).

Ported from ``../dexter/src/permissions/engine.ts`` (rule lookup, then
defaults, plus session-approved tools so a user isn't asked twice).
Defaults: read-only → allow, ``requires_approval`` → ask, otherwise allow.
``request_approval`` is wired to LangGraph ``interrupt()`` in P09; until
then an ``ask`` that isn't already session-approved is refused.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from app.domain.tools.base import ToolSpec

PermissionDecision = Literal["allow", "ask", "deny"]
ArgPredicate = Callable[[dict[str, Any]], bool]

# Arg-dependent approval (P07): writing the user's portfolio memory needs a human.
DEFAULT_ASK_WHEN: dict[str, ArgPredicate] = {
    "memory_update": lambda args: args.get("namespace") == "portfolio",
}


class PermissionPolicy:
    def __init__(
        self,
        rules: dict[str, PermissionDecision] | None = None,
        ask_when: dict[str, ArgPredicate] | None = None,
    ) -> None:
        self.rules: dict[str, PermissionDecision] = dict(rules or {})
        self.ask_when: dict[str, ArgPredicate] = dict(DEFAULT_ASK_WHEN if ask_when is None else ask_when)
        self.session_approved_tools: set[str] = set()

    def evaluate(self, spec: ToolSpec, args: dict[str, Any], run: Any = None) -> PermissionDecision:
        rule = self.rules.get(spec.name)
        if rule == "deny":
            return "deny"
        if rule == "allow":
            return "allow"
        predicate = self.ask_when.get(spec.name)
        if predicate is not None and predicate(args):
            return "ask"
        if rule == "ask" or spec.requires_approval:
            return "allow" if spec.name in self.session_approved_tools else "ask"
        return "allow"

    async def request_approval(self, spec: ToolSpec, args: dict[str, Any], run: Any = None) -> bool:
        raise NotImplementedError("human approval for tool calls is implemented in Phase 09")

    def approve_for_session(self, tool: str) -> None:
        self.session_approved_tools.add(tool)
