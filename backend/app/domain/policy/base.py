"""Deterministic policy gate (T4, contracts.md §9, phase-09 §9.6).

A ``PolicyRule`` never calls an LLM: it reads a proposal (and whatever
compute-derived context it needs) and returns a ``PolicyResult``. A
``PolicySet`` runs every rule and is ``blocked`` iff any ``block``-severity
rule failed. Concrete rules live in ``rules.py``; workflows in P10/P12 build
a ``PolicySet`` from settings-configured limits.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Severity = Literal["block", "warn"]


class PolicyResult(BaseModel):
    rule_id: str
    passed: bool
    severity: Severity
    message: str
    values: dict[str, Any] = Field(default_factory=dict)


class PolicyRule(Protocol):
    id: str
    description: str

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyResult: ...


class PolicyEvaluation(BaseModel):
    results: list[PolicyResult]
    blocked: bool

    @property
    def failed(self) -> list[PolicyResult]:
        return [r for r in self.results if not r.passed]

    @property
    def blocking_failures(self) -> list[PolicyResult]:
        return [r for r in self.results if not r.passed and r.severity == "block"]


class PolicySet:
    def __init__(self, rules: list[PolicyRule]) -> None:
        self.rules = rules

    def evaluate(self, proposal: dict[str, Any], context: dict[str, Any]) -> PolicyEvaluation:
        results = [rule.evaluate(proposal, context) for rule in self.rules]
        blocked = any(not r.passed and r.severity == "block" for r in results)
        return PolicyEvaluation(results=results, blocked=blocked)
