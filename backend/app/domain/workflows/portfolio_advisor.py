"""``portfolio_advisor`` workflow (P12 §12.3, F1, T4, T5): diagnose a book, propose a rebalance.

    load_portfolio → compute_analytics → recall_memory (P17) → diagnose (risk officer ∥ allocation
    strategist) → propose_rebalance (deep, structured ``RebalanceProposal``: target weights + reasons)
    → compute_trades (internal ``rebalance_plan``: lot-rounded trades, cash, post-trade metrics)
    → policy_gate → approval (interrupt) → save_plan → report → learn (P17)

- (P17 v2) ``recall_memory`` puts the user's INVESTOR PROFILE + ≤ 2 ReasoningBank lessons into the
  diagnose / propose prompts (after the limits) and supplies the goal from memory when the run has
  none (the report says so); ``learn`` turns the reviewer's answer into a lesson (approved/edited →
  strategy, rejected with a comment → pitfall + the comment's facts as memory). Memory off
  (``ctx.config["memory"]`` None) → both nodes do nothing;

- the two diagnosticians run concurrently inside one node (like the trading analysts):
  one checkpoint for the step, no fan-in races;
- the LLM proposes weights only; every share count, amount and metric comes from compute,
  and the proposed weights are recorded as an ``llm_assumption`` provenance record (F1/F3);
- target weights + cash that do not sum to 1 ± 0.001 are normalized *with a warning*;
  unknown symbols are dropped with a warning — never silently (§12.3);
- a policy block sends the advisor back once with the failures; a second block ends the run;
- ``save_plan`` stores an *approved plan* only — positions never change here. Applying a
  plan is a separate, explicit user action that writes a revision (§12.3).

Framework-neutral (F2): nodes use ``NodeContext`` only; the harness supplies the
``portfolio_loader`` and ``plan_store`` ports through ``config``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Protocol, TypedDict

from pydantic import BaseModel, Field
from ulid import ULID

from app.domain.agents.library import get_role, role_profile
from app.domain.agents.schemas import RebalanceProposal
from app.domain.memory.messages import context_blocks
from app.domain.memory.ports import MemoryPort
from app.domain.memory.schemas import OutcomeJudgement
from app.domain.models.decision import Decision, Verdict, route_decision
from app.domain.models.symbol import normalize_symbol, safe_path_component
from app.domain.policy import (
    CashSufficient,
    MaxPositionWeight,
    MaxSectorWeight,
    MaxTurnover,
    MinCashWeight,
    MinConfidence,
    NoSellList,
    PolicySet,
)
from app.domain.portfolio.analytics import PortfolioAnalytics, summary_text
from app.domain.ports import AgentRunRequest, LLMMessage, ToolCallReq
from app.domain.workflows.spec import (
    ApprovalRequest,
    NodeContext,
    NodeKind,
    NodeSpec,
    RouterSpec,
    WorkflowSpec,
)

RiskProfile = Literal["conservative", "balanced", "aggressive"]
DIAGNOSTICIANS = {"risk": "pf_risk_officer", "allocation": "pf_allocation_strategist"}
DIAGNOSE_MAX_ITERATIONS = 5
MAX_POLICY_ATTEMPTS = 2  # first proposal + one retry (§12.6)
WEIGHT_TOLERANCE = 0.001
CASH_ALIASES = frozenset({"CASH", "เงินสด"})
DEFAULT_PORTFOLIO_ID = "pf_default"
DEFAULT_USER_ID = "usr_default"

# Limits per risk profile; config (Settings page) replaces the profile default, a run's own
# constraint wins over both.
PROFILE_LIMITS: dict[str, dict[str, float]] = {
    "conservative": {"max_weight": 0.15, "max_sector": 0.30, "min_cash": 0.10, "max_turnover": 0.20},
    "balanced": {"max_weight": 0.20, "max_sector": 0.40, "min_cash": 0.05, "max_turnover": 0.30},
    "aggressive": {"max_weight": 0.30, "max_sector": 0.50, "min_cash": 0.0, "max_turnover": 0.40},
}


# -- input / output / state ---------------------------------------------------------------------


class AdviseConstraints(BaseModel):
    max_weight: float | None = Field(default=None, gt=0, le=1)
    max_sector: float | None = Field(default=None, gt=0, le=1)
    min_cash: float | None = Field(default=None, ge=0, lt=1)
    no_sell: list[str] = Field(default_factory=list, max_length=100)
    lot_size_th: int = Field(default=100, ge=1, le=10_000)
    max_turnover: float | None = Field(default=None, gt=0, le=2)


class AdviseInput(BaseModel):
    portfolio_id: str = Field(default=DEFAULT_PORTFOLIO_ID, min_length=1, max_length=40)
    goal: str | None = Field(default=None, max_length=1000)
    risk_profile: RiskProfile = "balanced"
    constraints: AdviseConstraints = Field(default_factory=AdviseConstraints)
    language: Literal["th", "en"] = "th"
    # Set by the API from the book at start time: a different book is a different graph thread (T3).
    portfolio_hash: str | None = None
    # (P17) whose memory (profile, goal fallback, what ``learn`` writes); not in the graph signature
    user_id: str = DEFAULT_USER_ID


class AdviseOutput(BaseModel):
    portfolio_id: str
    status: str
    plan_id: str | None = None
    report_md: str = ""


class AdvisorState(TypedDict, total=False):
    input: dict[str, Any]
    portfolio: dict[str, Any]
    analytics: dict[str, Any]
    diagnoses: dict[str, Any]
    proposal: dict[str, Any]
    plan: dict[str, Any]
    policy: dict[str, Any]
    approval: dict[str, Any]
    saved_plan: dict[str, Any]
    report_md: str
    outcome: dict[str, Any]
    memory: dict[str, Any]
    learned: dict[str, Any]


class PortfolioLoaderPort(Protocol):
    async def __call__(self, portfolio_id: str) -> dict[str, Any] | None: ...


class PlanStorePort(Protocol):
    async def save(self, record: dict[str, Any]) -> str: ...


def effective_limits(inp: AdviseInput, config: Any) -> dict[str, float]:
    profile = dict(PROFILE_LIMITS[inp.risk_profile])
    for key, value in (config.get("limit_overrides") or {}).items():
        if key in profile and value is not None:
            profile[key] = float(value)
    c = inp.constraints
    for key in ("max_weight", "max_sector", "min_cash", "max_turnover"):
        value = getattr(c, key)
        if value is not None:
            profile[key] = float(value)
    profile["min_confidence"] = float(config.get("min_confidence", 0.7))  # same floor as trading
    return profile


# -- helpers ------------------------------------------------------------------------------------


async def _tool(ctx: NodeContext, name: str, args: dict[str, Any]) -> Any:
    (result,) = await ctx.tools.run_calls([ToolCallReq(id=f"call_{ULID()}", name=name, args=args)])
    return result


def _input(state: dict[str, Any]) -> AdviseInput:
    return AdviseInput.model_validate(state["input"])


def _lang(state: dict[str, Any]) -> str:
    return "Thai" if state["input"].get("language", "th") == "th" else "English"


def _system(role: str, state: dict[str, Any]) -> str:
    return f"{role_profile(get_role(role))}\n\nWrite in {_lang(state)}. Keep tickers and numbers as given."


def _analytics(state: dict[str, Any]) -> PortfolioAnalytics:
    return PortfolioAnalytics.model_validate(state["analytics"]["data"])


def goal_of(state: dict[str, Any]) -> tuple[str | None, bool]:
    """(P17) The run's goal, or the user's goal from memory when none was given → (goal, from_memory)."""
    given = (state["input"].get("goal") or "").strip()
    if given:
        return given, False
    remembered = (state.get("memory") or {}).get("goal")
    return (str(remembered), True) if remembered else (None, False)


def _limits_text(inp: AdviseInput, limits: dict[str, float], state: dict[str, Any] | None = None) -> str:
    no_sell = ", ".join(inp.constraints.no_sell) or "none"
    goal, from_memory = goal_of(state) if state is not None else (inp.goal, False)
    text = (
        f"Risk profile: {inp.risk_profile}. Goal: {goal or '(not given)'}"
        + (" (from the user's memory)" if from_memory else "")
        + ".\n"
        f"Policy: max weight per symbol {limits['max_weight']:.0%}, "
        f"max sector {limits['max_sector']:.0%}, min cash {limits['min_cash']:.0%}, "
        f"max turnover {limits['max_turnover']:.0%}, never sell: {no_sell}."
    )
    if state is not None:  # (P17) INVESTOR PROFILE + LESSONS right after the limits
        mem = state.get("memory") or {}
        blocks = context_blocks(
            profile_text=mem.get("profile_text") or "", lessons=mem.get("reasoning") or []
        )
        if blocks:
            text += "\n\n" + "\n\n".join(blocks)
    return text


async def _warn(ctx: NodeContext, code: str, message: str) -> None:
    await ctx.emit.emit("warning", {"code": code, "message": message})


# -- nodes: load + analytics --------------------------------------------------------------------


async def load_portfolio(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    inp = _input(state)
    loader: PortfolioLoaderPort | None = ctx.config.get("portfolio_loader")
    data = await loader(inp.portfolio_id) if loader is not None else None
    if data is None:
        value = {"available": False, "state": "not_provided"}
        return {"portfolio": value, "_summary": f"{inp.portfolio_id}: not provided"}
    positions = data.get("positions") or []
    value = {"available": True, "state": "positions" if positions else "flat", "data": data}
    return {"portfolio": value, "_summary": f"{data.get('name')}: {len(positions)} positions"}


def route_after_load(state: dict[str, Any]) -> str:
    pf = state.get("portfolio") or {}
    return "ok" if pf.get("state") == "positions" else "empty"


async def compute_analytics(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    res = await _tool(
        ctx, "portfolio_analytics", {"portfolio": state["portfolio"]["data"], "purpose": "compute_analytics"}
    )
    if not res.ok or not isinstance(res.data, dict):
        return {"analytics": {"available": False, "error": res.summary}, "_summary": f"failed: {res.summary}"}
    for w in res.data.get("warnings") or []:
        await _warn(ctx, w["code"], w["message"])
    valued = res.data.get("total_value") is not None and not res.data.get("weights_declared_only")
    value = {
        "available": True,
        "valued": valued,
        "data": res.data,
        "provenance_ids": list(res.provenance_ids),
    }
    return {"analytics": value, "_summary": res.summary}


def route_after_analytics(state: dict[str, Any]) -> str:
    a = state.get("analytics") or {}
    if not a.get("available"):
        return "unavailable"
    return "ok" if a.get("valued") else "unvalued"


# -- nodes: memory (P17) ------------------------------------------------------------------------


def _memory_port(ctx: NodeContext) -> MemoryPort | None:
    memory: MemoryPort | None = ctx.config.get("memory")
    return memory


async def recall_memory(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    """The user's profile + ≤ 2 ReasoningBank lessons (``workflow=portfolio``) for diagnose/propose.

    Not part of the thread id: a resumed run recalls again.
    """
    memory = _memory_port(ctx)
    if memory is None:
        return {"memory": {"enabled": False}, "_summary": "memory off"}
    inp = _input(state)
    query = " ".join(
        [inp.goal or "portfolio rebalance goal เป้าหมาย", inp.risk_profile]
        + [h.symbol for h in _analytics(state).holdings[:10]]
    )
    try:
        recall = await memory.recall(inp.user_id, query, workflow="portfolio")
        if recall.reasoning:
            await memory.bank.record_use([r.id for r in recall.reasoning], ctx.run_id, inp.user_id)
    except Exception as exc:  # memory never breaks a run
        await _warn(ctx, "memory_recall_failed", f"{type(exc).__name__}: {exc}")
        return {"memory": {"enabled": True, "error": str(exc)}, "_summary": "recall failed"}
    goals = [i for i in recall.profile_items if i.category == "goal" and i.status == "active"]
    items = [{"id": i.id, "category": i.category, "text": i.text, "status": i.status} for i in recall.items]
    reasoning = [
        {"id": r.id, "title": r.title, "description": r.description, "content": r.content}
        for r in recall.reasoning
    ]
    await ctx.emit.emit(
        "memory.recalled",
        {"items": items, "reasoning": [{"id": r["id"], "title": r["title"]} for r in reasoning]},
    )
    value = {
        "enabled": True,
        "profile_text": recall.profile_text,
        "items": items,
        "reasoning": reasoning,
        "goal": goals[0].text if goals else None,
        "provenance": recall.provenance,
    }
    summary = f"profile {recall.token_estimate} tokens · {len(reasoning)} lessons" + (
        " · goal from memory" if not inp.goal and goals else ""
    )
    return {"memory": value, "_summary": summary}


# -- nodes: diagnose (concurrent) ---------------------------------------------------------------


async def diagnose(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    inp = _input(state)
    limits = effective_limits(inp, ctx.config)
    base = (
        f"PORTFOLIO ANALYTICS (computed ground truth; provenance ids in [^…]):\n"
        f"{summary_text(_analytics(state))}\n\n{_limits_text(inp, limits, state)}"
    )

    async def run(key: str, role: str) -> dict[str, Any]:
        result = await ctx.agent.run_loop(
            AgentRunRequest(
                role=role,
                instruction=f"{base}\n\nWrite your diagnosis of this portfolio in {_lang(state)}.",
                max_iterations=DIAGNOSE_MAX_ITERATIONS,
            )
        )
        return {"role": role, "text": result.draft, "tool_calls": result.tool_calls,
                "stop_reason": result.stop_reason}  # fmt: skip

    keys = list(DIAGNOSTICIANS)
    outcomes = await asyncio.gather(*(run(k, DIAGNOSTICIANS[k]) for k in keys), return_exceptions=True)
    diagnoses: dict[str, Any] = {}
    for key, out in zip(keys, outcomes, strict=True):
        if isinstance(out, BaseException):
            if isinstance(out, asyncio.CancelledError):
                raise out
            diagnoses[key] = {
                "role": DIAGNOSTICIANS[key],
                "text": "",
                "error": f"{type(out).__name__}: {out}",
            }
            await _warn(ctx, "diagnosis_failed", f"{key}: {out}")
        else:
            diagnoses[key] = out
    ok = sum(1 for d in diagnoses.values() if d.get("text"))
    return {"diagnoses": diagnoses, "_summary": f"{ok}/{len(keys)} diagnoses"}


def _diagnoses_text(state: dict[str, Any]) -> str:
    parts = []
    for key, d in (state.get("diagnoses") or {}).items():
        parts.append(f"### {key}\n{d.get('text') or '(unavailable: ' + str(d.get('error')) + ')'}")
    return "\n\n".join(parts) or "(no diagnoses)"


# -- nodes: propose -----------------------------------------------------------------------------


def _weights_table(a: PortfolioAnalytics) -> str:
    rows = [
        f"- {h.symbol}: current {h.weight.value:.2%}, sector {h.sector}"
        + (f", user target {h.target_weight:.2%}" if h.target_weight is not None else "")
        for h in a.holdings
        if h.weight is not None
    ]
    cash = a.weights.get("CASH", 0.0)
    return "\n".join(rows) + f"\n- CASH: current {cash:.2%}"


def normalize_proposal(
    proposal: RebalanceProposal, a: PortfolioAnalytics
) -> tuple[dict[str, float], float, dict[str, str], list[dict[str, str]]]:
    """LLM proposal → (targets by canonical symbol, cash target, rationales, warnings). Pure.

    Held-but-unvalued symbols are left out (the plan cannot trade what it cannot price);
    a valued holding missing from the targets is sold to 0 (with a warning).
    """
    warnings: list[dict[str, str]] = []
    valued = {h.symbol for h in a.holdings if h.market_value is not None}
    unvalued = {h.symbol for h in a.holdings if h.market_value is None}
    declared_new: set[str] = set()
    for raw in proposal.new_symbols:
        try:
            declared_new.add(normalize_symbol(raw).canonical)
        except ValueError:
            warnings.append(
                {"code": "invalid_symbol", "message": f"new symbol {raw!r} is not valid — dropped"}
            )
    targets: dict[str, float] = {}
    rationale: dict[str, str] = {}
    cash = proposal.cash_target
    for t in proposal.targets:
        if t.symbol.strip().upper() in CASH_ALIASES:  # models often list cash as a "symbol"
            cash += t.target_weight
            warnings.append({"code": "cash_as_symbol", "message": f"{t.symbol!r} target folded into cash"})
            continue
        try:
            sym = normalize_symbol(t.symbol)
            safe_path_component(sym.canonical)
        except ValueError:
            warnings.append(
                {"code": "invalid_symbol", "message": f"target {t.symbol!r} is not valid — dropped"}
            )
            continue
        c = sym.canonical
        if c in unvalued:
            warnings.append({"code": "unvalued_target", "message": f"{c} has no price — left unchanged"})
            continue
        if c not in valued and c not in declared_new:
            warnings.append(
                {"code": "unlisted_new_symbol", "message": f"{c} is not held; treated as a new symbol"}
            )
        targets[c] = targets.get(c, 0.0) + t.target_weight
        if t.rationale:
            rationale[c] = t.rationale
    for c in sorted(valued - set(targets)):
        targets[c] = 0.0
        warnings.append(
            {"code": "holding_omitted", "message": f"{c} was not in the proposal — target 0 (sell)"}
        )
    total = sum(targets.values()) + cash
    if total <= 0:
        return (
            {},
            0.0,
            rationale,
            [*warnings, {"code": "empty_proposal", "message": "all target weights are 0"}],
        )
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        warnings.append(
            {
                "code": "weights_normalized",
                "message": f"target weights + cash summed to {total:.2%}; scaled to 100%",
            }
        )
        targets = {k: v / total for k, v in targets.items()}
        cash = cash / total
    return targets, cash, rationale, warnings


def _previous_plan_text(state: dict[str, Any]) -> str:
    """What the blocked plan computed, so the retry can aim (e.g. smaller moves for turnover)."""
    plan = state.get("plan") or {}
    if not plan.get("ok"):
        return ""
    moves = ", ".join(
        f"{leg['symbol']} {leg['weight_before']:.1%}→{leg['weight_after']:.1%}"
        for leg in plan.get("legs") or []
        if leg["action"] != "hold"
    )
    return (
        f"Computed from that proposal: turnover {plan.get('turnover', 0):.1%}, "
        f"cash after {plan.get('cash_weight_after', 0):.1%}; moves: {moves or 'none'}. "
        "Turnover is the sum of all traded value / portfolio value: smaller moves lower it."
    )


async def propose_rebalance(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    inp = _input(state)
    limits = effective_limits(inp, ctx.config)
    a = _analytics(state)
    previous = state.get("policy") or {}
    failures = [
        r for r in previous.get("results", []) if not r.get("passed") and r.get("severity") == "block"
    ]
    attempt = int(previous.get("attempts") or 0) + 1
    user = (
        f"PORTFOLIO ANALYTICS:\n{summary_text(a)}\n\n## Current weights\n{_weights_table(a)}\n\n"
        f"{_limits_text(inp, limits, state)}\n\n## Diagnoses\n{_diagnoses_text(state)}\n\n"
        + (
            "## Your previous proposal was blocked by the policy — fix it:\n"
            + "\n".join(f"- {f['rule_id']}: {f['message']}" for f in failures)
            + f"\n{_previous_plan_text(state)}\n\n"
            if failures
            else ""
        )
        + "Return the rebalance proposal (target weights 0-1 + cash_target summing to 1)."
    )
    messages = [
        LLMMessage(role="system", content=_system("pf_advisor", state)),
        LLMMessage(role="user", content=user),
    ]
    result = await ctx.llm.structured(messages, RebalanceProposal, tier="deep", purpose="structured")
    model = result.value
    if model is None:
        decision = Decision(verdict=Verdict.REVIEW, confidence=0.0, reason="advisor output unparseable")
        value = {"available": False, "decision": decision.model_dump(mode="json"), "verdict": "REVIEW",
                 "mode": result.mode, "attempt": attempt, "warnings": []}  # fmt: skip
        await _warn(ctx, "structured_failed", "advisor proposal could not be parsed")
        return {"proposal": value, "_summary": "unparseable"}
    targets, cash, rationale, warnings = normalize_proposal(model, a)
    for w in warnings:
        await _warn(ctx, w["code"], w["message"])
    verdict = route_decision(model.decision, min_confidence=0.0)  # confidence is checked by the policy
    value = {
        "available": bool(targets),
        "targets": targets,
        "cash_target": cash,
        "rationale": rationale,
        "raw_targets": [t.model_dump() for t in model.targets],
        "raw_cash_target": model.cash_target,
        "new_symbols": model.new_symbols,
        "summary": model.summary,
        "decision": model.decision.model_dump(mode="json"),
        "verdict": verdict.value,
        "mode": result.mode,
        "attempt": attempt,
        "warnings": warnings,
    }
    return {
        "proposal": value,
        "_summary": f"{len(targets)} targets · cash {cash:.1%} · {verdict.value} "
        f"({model.decision.confidence:.2f})" + (f" · {len(warnings)} warnings" if warnings else ""),
    }


def route_after_proposal(state: dict[str, Any]) -> str:
    p = state.get("proposal") or {}
    if not p.get("available"):
        return "none"
    return "no_change" if p.get("verdict") == Verdict.REJECT.value else "compute"


# -- nodes: compute trades + policy -------------------------------------------------------------


async def _plan(
    state: dict[str, Any],
    ctx: NodeContext,
    targets: dict[str, float],
    *,
    attempt: str,
    agent: str = "pf_advisor",
) -> dict[str, Any]:
    inp = _input(state)
    a = _analytics(state)
    holdings = [
        {
            "symbol": h.symbol,
            "currency": h.currency,
            "price": h.price.value if h.price else None,
            "market_value": h.market_value.value if h.market_value else None,
            "sector": h.sector,
        }
        for h in a.holdings
    ]
    args = {
        "base_currency": a.base_currency,
        "holdings": holdings,
        "cash": a.cash.value if a.cash else 0.0,
        "fx": {ccy: pv.value for ccy, pv in a.fx.items()},
        "targets": targets,
        "lot_size_th": inp.constraints.lot_size_th,
        "attempt": attempt,
        "assumption": {"agent": agent},
        "input_provenance_ids": [pid for pid in [a.weights_provenance_id] if pid],
    }
    res = await _tool(ctx, "rebalance_plan", args)
    if not res.ok or not isinstance(res.data, dict) or not res.data.get("ok"):
        return {"ok": False, "error": res.summary, "provenance_ids": list(res.provenance_ids)}
    for note in res.data.get("notes") or []:
        await _warn(ctx, "plan_note", note)
    return {**res.data, "provenance_ids": list(res.provenance_ids)}


async def compute_trades(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    p = state["proposal"]
    plan = await _plan(state, ctx, p["targets"], attempt=f"propose#{p.get('attempt', 1)}")
    if not plan.get("ok"):
        return {"plan": plan, "_summary": f"failed: {plan.get('error')}"}
    trades = sum(1 for leg in plan["legs"] if leg["action"] != "hold")
    summary = f"{trades} trades · turnover {plan['turnover']:.1%} · cash after {plan['cash_after']:,.0f}"
    return {"plan": plan, "_summary": summary}


def route_after_trades(state: dict[str, Any]) -> str:
    return "gate" if (state.get("plan") or {}).get("ok") else "failed"


def advisor_policy(limits: dict[str, float]) -> PolicySet:
    return PolicySet(
        [
            MaxPositionWeight(limits["max_weight"]),
            MaxSectorWeight(limits["max_sector"]),
            CashSufficient(),
            MinCashWeight(limits["min_cash"]),
            NoSellList(),
            MaxTurnover(limits["max_turnover"]),
            MinConfidence(limits["min_confidence"], severity="warn"),  # a human approves every plan
        ]
    )


def evaluate_plan(
    plan: dict[str, Any], confidence: float, inp: AdviseInput, limits: dict[str, float]
) -> dict[str, Any]:
    weights = {k: v for k, v in (plan.get("weights_after") or {}).items() if k != "CASH"}
    sectors = {k: v for k, v in (plan.get("sector_after") or {}).items() if k not in ("CASH", "Unknown")}
    top_symbol = max(weights, key=lambda k: weights[k]) if weights else None
    top_sector = max(sectors, key=lambda k: sectors[k]) if sectors else None
    proposal = {
        "position_weight": weights[top_symbol] if top_symbol else 0.0,
        "sector_weight": sectors[top_sector] if top_sector else 0.0,
        "cash_required": plan.get("cash_required"),
        "cash_weight": plan.get("cash_weight_after"),
        "sells": [leg["symbol"] for leg in plan.get("legs") or [] if leg["shares"] < 0],
        "turnover": plan.get("turnover"),
        "confidence": confidence,
    }
    no_sell = []
    for raw in inp.constraints.no_sell:
        try:
            no_sell.append(normalize_symbol(raw).canonical)
        except ValueError:
            continue
    context = {"cash_available": plan.get("cash_before"), "no_sell": no_sell}
    evaluation = advisor_policy(limits).evaluate(proposal, context)
    results = [r.model_dump(mode="json") for r in evaluation.results]
    for r in results:  # name the holding / sector the max-rules looked at
        if r["rule_id"] == "max_position_weight" and top_symbol:
            r["values"]["symbol"] = top_symbol
            r["message"] = f"{top_symbol}: {r['message']}"
        if r["rule_id"] == "max_sector_weight" and top_sector:
            r["values"]["sector"] = top_sector
            r["message"] = f"{top_sector}: {r['message']}"
    return {"blocked": evaluation.blocked, "results": results}


async def policy_gate(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    inp = _input(state)
    limits = effective_limits(inp, ctx.config)
    confidence = float(state["proposal"]["decision"]["confidence"])
    evaluation = evaluate_plan(state["plan"], confidence, inp, limits)
    attempts = int((state.get("policy") or {}).get("attempts") or 0) + 1
    failed = [r["rule_id"] for r in evaluation["results"] if not r["passed"]]
    value = {**evaluation, "attempts": attempts, "limits": limits}
    return {
        "policy": value,
        "_summary": ("blocked: " if evaluation["blocked"] else "passed ") + ", ".join(failed),
    }


def route_after_policy(state: dict[str, Any]) -> str:
    policy = state["policy"]
    if not policy["blocked"]:
        return "approve"
    return "retry" if int(policy["attempts"]) < MAX_POLICY_ATTEMPTS else "reject"


# -- nodes: approval + save + report ------------------------------------------------------------


def _metrics(a: PortfolioAnalytics) -> dict[str, Any]:
    sectors = {k: v for k, v in (a.exposure.get("sector") or {}).items() if k not in ("CASH", "Unknown")}
    c = a.concentration
    return {
        "hhi": c.hhi if c else None,
        "top1": c.top1 if c else None,
        "effective_n": c.effective_n if c else None,
        "cash_weight": a.weights.get("CASH"),
        "max_sector": max(sectors.values()) if sectors else None,
    }


def _metrics_after(plan: dict[str, Any]) -> dict[str, Any]:
    sectors = {k: v for k, v in (plan.get("sector_after") or {}).items() if k not in ("CASH", "Unknown")}
    c = plan.get("concentration_after") or {}
    return {
        "hhi": c.get("hhi"),
        "top1": c.get("top1"),
        "effective_n": c.get("effective_n"),
        "cash_weight": plan.get("cash_weight_after"),
        "max_sector": max(sectors.values()) if sectors else None,
    }


def plan_card(state: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, Any]:
    a = _analytics(state)
    p = state["proposal"]
    plan = plan or state["plan"]
    rows = []
    for leg in plan.get("legs") or []:
        rows.append(
            {
                "symbol": leg["symbol"],
                "current": leg["weight_before"],
                "target": leg["weight_target"],
                "after": leg["weight_after"],
                "delta": leg["weight_after"] - leg["weight_before"],
                "action": leg["action"],
                "shares": leg["shares"],
                "value": leg["value"],
                "rationale": (p.get("rationale") or {}).get(leg["symbol"], ""),
            }
        )
    return {
        "portfolio_id": a.portfolio_id,
        "name": a.name,
        "base_currency": a.base_currency,
        "total_value": plan.get("total_value"),
        "rows": rows,
        "cash_before": plan.get("cash_before"),
        "cash_after": plan.get("cash_after"),
        "cash_target": p.get("cash_target"),
        "turnover": plan.get("turnover"),
        "metrics_before": _metrics(a),
        "metrics_after": _metrics_after(plan),
        "summary": p.get("summary"),
        "verdict": p.get("verdict"),
        "confidence": p["decision"]["confidence"],
        "warnings": [w["message"] for w in p.get("warnings") or []],
        "provenance": plan.get("provenance") or {},
    }


def _edited_targets(edits: dict[str, Any], state: dict[str, Any]) -> dict[str, float]:
    """Approval edit ``{"target_weights": {symbol: w}, "cash_target"?: w}`` → normalized targets."""
    raw = edits.get("target_weights")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("edits.target_weights must be a non-empty {symbol: weight} map")
    base = dict(state["proposal"]["targets"])
    for symbol, w in raw.items():
        c = normalize_symbol(str(symbol)).canonical
        weight = float(w)
        if not 0 <= weight <= 1:
            raise ValueError(f"weight of {c} must be within 0..1")
        if c not in base:
            raise ValueError(f"{c} is not part of the proposal")
        base[c] = weight
    cash = float(edits.get("cash_target", state["proposal"]["cash_target"]))
    if not 0 <= cash < 1:
        raise ValueError("cash_target must be within 0..1")
    total = sum(base.values()) + cash
    if total <= 0:
        raise ValueError("all weights are 0")
    return {k: v / total for k, v in base.items()}


async def approval(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    card = plan_card(state)
    response = await ctx.request_approval(
        ApprovalRequest(
            kind="rebalance_plan",
            title=f"Rebalance {card['name']}: {sum(1 for r in card['rows'] if r['action'] != 'hold')} trades",
            proposal=card,
            policy_results=state["policy"]["results"],
            provenance_ids=list(state["plan"].get("provenance_ids") or []),
        )
    )
    value: dict[str, Any] = {
        "outcome": response.outcome,
        "comment": response.comment,
        "edits": response.edits,
        # what the human answered, before an invalid/blocked edit turns it into "rejected" (P17 learn)
        "human_outcome": response.outcome,
        "human_comment": response.comment,
    }
    if response.outcome == "edited":
        # The human edits target weights only; trades, cash and metrics are recomputed and
        # the policy runs again before anything is saved (T4).
        try:
            targets = _edited_targets(response.edits or {}, state)
        except (TypeError, ValueError) as exc:
            value.update(outcome="rejected", comment=f"invalid edit: {exc}")
            return {"approval": value, "_summary": "rejected"}
        plan = await _plan(state, ctx, targets, attempt="approval#edit", agent="user")
        if not plan.get("ok"):
            value.update(outcome="rejected", comment=f"edit could not be planned: {plan.get('error')}")
            return {"approval": value, "_summary": "rejected"}
        inp = _input(state)
        evaluation = evaluate_plan(
            plan, float(state["proposal"]["decision"]["confidence"]), inp, effective_limits(inp, ctx.config)
        )
        value.update(targets=targets, plan=plan, policy=evaluation)
        if evaluation["blocked"]:
            blocked = [
                r["rule_id"] for r in evaluation["results"] if not r["passed"] and r["severity"] == "block"
            ]
            value.update(outcome="rejected", comment="edit blocked by policy: " + ", ".join(blocked))
    return {"approval": value, "_summary": value["outcome"]}


def route_after_approval(state: dict[str, Any]) -> str:
    return "save" if state["approval"]["outcome"] in ("approved", "edited") else "report"


async def save_plan(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    appr = state["approval"]
    plan = appr.get("plan") or state["plan"]
    card = plan_card(state, plan)
    record = {
        "portfolio_id": state["portfolio"]["data"]["id"],
        "run_id": ctx.run_id,
        "status": "approved",
        "proposal": {
            "card": card,
            "targets": appr.get("targets") or state["proposal"]["targets"],
            "cash_target": state["proposal"]["cash_target"],
            "legs": plan.get("legs") or [],
            "portfolio_hash": state["input"].get("portfolio_hash"),
        },
        "response": {"outcome": appr["outcome"], "comment": appr.get("comment"), "edits": appr.get("edits")},
    }
    store: PlanStorePort | None = ctx.config.get("plan_store")
    plan_id = await store.save(record) if store is not None else f"plan_{ctx.run_id}"
    return {"saved_plan": {"plan_id": plan_id}, "_summary": f"saved {plan_id} (positions unchanged)"}


def _outcome(state: dict[str, Any]) -> dict[str, Any]:
    pf = state.get("portfolio") or {}
    if pf.get("state") == "not_provided":
        return {"status": "not_provided", "reason": "ยังไม่มีพอร์ตนี้ — กรอกพอร์ตก่อน"}
    if pf.get("state") == "flat":
        return {"status": "empty", "reason": "พอร์ตยังไม่มี position"}
    a = state.get("analytics") or {}
    if not a.get("available"):
        return {"status": "unavailable", "reason": a.get("error")}
    if not a.get("valued"):
        return {"status": "unvalued", "reason": "ต้องมีจำนวนหุ้นและราคาจึงเสนอ rebalance ได้ (พอร์ตแบบ % อย่างเดียว)"}
    p = state.get("proposal") or {}
    if not p.get("available"):
        return {"status": "no_proposal", "reason": p.get("decision", {}).get("reason") or "no valid targets"}
    if p.get("verdict") == Verdict.REJECT.value:
        return {"status": "no_change", "reason": p["decision"]["reason"]}
    plan = state.get("plan") or {}
    if not plan.get("ok"):
        return {"status": "unavailable", "reason": plan.get("error")}
    policy = state.get("policy") or {}
    if policy.get("blocked"):
        blocked = [r["rule_id"] for r in policy["results"] if not r["passed"] and r["severity"] == "block"]
        return {"status": "rejected", "reason": "policy blocked: " + ", ".join(blocked)}
    appr = state.get("approval") or {}
    if appr.get("outcome") in ("approved", "edited"):
        return {"status": "approved", "reason": appr.get("comment"),
                "plan_id": (state.get("saved_plan") or {}).get("plan_id")}  # fmt: skip
    return {"status": "declined", "reason": appr.get("comment") or "rejected by reviewer"}


def _pct(x: Any) -> str:
    return f"{x:.2%}" if isinstance(x, int | float) else "-"


def _num(x: Any) -> str:
    return f"{x:,.2f}" if isinstance(x, int | float) else "-"


def render_plan(state: dict[str, Any]) -> str:
    if not (state.get("plan") or {}).get("ok"):
        return ""
    appr = state.get("approval") or {}
    card = plan_card(state, appr.get("plan"))
    table = "| Symbol | Current | Target | After | Action | Shares | Value |\n|---|---|---|---|---|---|---|\n"
    table += "\n".join(
        f"| {r['symbol']} | {_pct(r['current'])} | {_pct(r['target'])} | {_pct(r['after'])} "
        f"| {r['action']} | {r['shares']:,} | {_num(r['value'])} |"
        for r in card["rows"]
    )
    before, after = card["metrics_before"], card["metrics_after"]
    metrics = (
        "| Metric | Before | After |\n|---|---|---|\n"
        f"| HHI | {_num(before['hhi'])} | {_num(after['hhi'])} |\n"
        f"| Largest weight | {_pct(before['top1'])} | {_pct(after['top1'])} |\n"
        f"| Effective N | {_num(before['effective_n'])} | {_num(after['effective_n'])} |\n"
        f"| Cash | {_pct(before['cash_weight'])} | {_pct(after['cash_weight'])} |\n"
        f"| Largest sector | {_pct(before['max_sector'])} | {_pct(after['max_sector'])} |"
    )
    policy = appr.get("policy") or state.get("policy") or {}
    checks = "\n".join(f"- {_mark(r)} {r['rule_id']}: {r['message']}" for r in policy.get("results", []))
    cash = f"Cash {_num(card['cash_before'])} → {_num(card['cash_after'])}"
    return (
        f"## Plan (computed, {card['base_currency']})\n\n{table}\n\n"
        f"{cash} · turnover {_pct(card['turnover'])}\n\n"
        f"## Before → after\n\n{metrics}\n\n## Policy\n\n{checks or '(not evaluated)'}"
    )


def _mark(r: dict[str, Any]) -> str:
    if r["passed"]:
        return "✓"
    return "✗" if r["severity"] == "block" else "!"


async def report(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    outcome = _outcome(state)
    name = ((state.get("portfolio") or {}).get("data") or {}).get("name") or state["input"].get(
        "portfolio_id"
    )
    parts = [f"# Rebalance — {name} — {outcome['status']}"]
    if outcome.get("reason"):
        parts.append(f"> {outcome['reason']}")
    goal, from_memory = goal_of(state)
    if from_memory:  # (P17) say so: the user did not type this goal for this run
        parts.append(f"_Goal from your memory: {goal}_")
    diag = state.get("diagnoses") or {}
    if diag:
        body = "\n\n".join(
            f"**{k}**\n\n{(d.get('text') or '(unavailable)').strip()}" for k, d in diag.items()
        )
        parts.append(f"## Diagnosis\n\n{body}")
    p = state.get("proposal") or {}
    if p.get("summary"):
        parts.append(f"## Proposal\n\n{p['summary']}")
    if p.get("warnings"):
        parts.append("## Warnings\n\n" + "\n".join(f"- {w['message']}" for w in p["warnings"]))
    numbers = render_plan(state)
    if numbers:
        parts.append(numbers)
    parts.append(
        "---\n_Plan only: positions were not changed (apply an approved plan from the Portfolio page). "
        "No order was sent to any broker._"
    )
    markdown = "\n\n".join(parts)
    await ctx.emit.emit("answer.final", {"markdown": markdown, "citations": [], "unverified_numbers": []})
    return {"report_md": markdown, "outcome": outcome, "_summary": outcome["status"]}


def _advisor_summary(state: dict[str, Any], *, success: bool, comment: str | None) -> str:
    p = state.get("proposal") or {}
    plan = (state.get("approval") or {}).get("plan") or state.get("plan") or {}
    policy = (state.get("approval") or {}).get("policy") or state.get("policy") or {}
    checks = ", ".join(f"{r['rule_id']} {'ok' if r['passed'] else 'fail'}" for r in policy.get("results", []))
    trades = sum(1 for leg in plan.get("legs") or [] if leg.get("action") != "hold")
    goal, from_memory = goal_of(state)
    return "\n".join(
        [
            f"Risk profile: {state['input'].get('risk_profile')} · goal: {goal or 'none'}"
            + (" (from memory)" if from_memory else ""),
            f"Diagnoses:\n{_diagnoses_text(state)[:2500]}",
            f"Proposal: {p.get('summary') or ''} (verdict {p.get('verdict')})",
            f"Plan: {trades} trades, turnover {plan.get('turnover', 0) or 0:.1%}",
            f"Policy: {checks or 'not evaluated'}",
            "Reviewer: " + ("approved" if success else "rejected") + (f' — "{comment}"' if comment else ""),
        ]
    )


async def learn(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    """(P17) After the report: approved/edited → a strategy lesson; rejected → the recalled lessons
    failed, and with a comment → a pitfall lesson + facts from the comment ("never sell KBANK")."""
    memory = _memory_port(ctx)
    appr = state.get("approval") or {}
    human = appr.get("human_outcome") or appr.get("outcome")
    if memory is None or human is None:
        return {"learned": {"enabled": memory is not None, "judged": False}, "_summary": "nothing to learn"}
    inp = _input(state)
    success = human in ("approved", "edited") and appr.get("outcome") in ("approved", "edited")
    comment = (appr.get("human_comment") or "").strip() if human == "rejected" else ""
    out: dict[str, Any] = {
        "enabled": True,
        "judged": True,
        "success": success,
        "changes": [],
        "distilled": [],
    }
    if comment:
        try:
            changes = await memory.learn(
                inp.user_id,
                user_messages=[comment],
                source_ref={"run_id": ctx.run_id, "entered": "advisor_rejection"},
                llm=ctx.llm,
                emit=ctx.emit,
                node="learn",
            )
            out["changes"] = [
                {"op": c.op, "id": c.item.id, "category": c.item.category, "text": c.item.text}
                for c in changes
                if c.op != "NOOP"
            ]
        except Exception as exc:
            await _warn(ctx, "memory_learn_failed", f"{type(exc).__name__}: {exc}")
    try:
        if success or comment:
            items = await memory.bank.distill(
                run_summary=_advisor_summary(state, success=success, comment=comment or None),
                outcome=OutcomeJudgement(
                    success=success,
                    score=1.0 if success else 0.0,
                    reason="plan approved by the user" if success else "plan rejected by the user",
                ),
                tags={"workflow": "portfolio"},
                source_run_id=ctx.run_id,
                user_id=inp.user_id,
                max_items=2,
                llm=ctx.llm,
                emit=ctx.emit,
                node="learn",
            )
            out["distilled"] = [{"id": i.id, "title": i.title, "kind": i.kind} for i in items]
        await memory.bank.settle(ctx.run_id, success)
    except Exception as exc:
        await _warn(ctx, "memory_learn_failed", f"ReasoningBank: {type(exc).__name__}: {exc}")
    summary = ("success" if success else "failure") + f" · {len(out['distilled'])} lessons"
    return {"learned": out, "_summary": summary}


def output_of(state: dict[str, Any]) -> AdviseOutput:
    outcome = state.get("outcome") or {}
    return AdviseOutput(
        portfolio_id=state["input"].get("portfolio_id", DEFAULT_PORTFOLIO_ID),
        status=outcome.get("status", "unknown"),
        plan_id=outcome.get("plan_id"),
        report_md=state.get("report_md", ""),
    )


# -- spec ---------------------------------------------------------------------------------------


def _node(id_: str, label: str, kind: NodeKind, phase: str, fn: Any, writes: set[str], **kw: Any) -> NodeSpec:
    return NodeSpec(id=id_, label=label, kind=kind, phase=phase, fn=fn, writes=frozenset(writes), **kw)


def _router(source: str, fn: Any, path_map: dict[str, str]) -> RouterSpec:
    returns = frozenset(k for k in path_map if k != "__fallback__")
    return RouterSpec(source=source, fn=fn, path_map=path_map, returns=returns)


PORTFOLIO_ADVISOR_WORKFLOW = WorkflowSpec(
    id="portfolio_advisor",
    version="2",
    input_model=AdviseInput,
    output_model=AdviseOutput,
    state_model=AdvisorState,
    nodes=[
        _node(
            "load_portfolio", "Load portfolio", NodeKind.COMPUTE, "retrieve", load_portfolio, {"portfolio"}
        ),
        _node(
            "compute_analytics", "Analytics", NodeKind.COMPUTE, "compute", compute_analytics, {"analytics"}
        ),
        _node("recall_memory", "Recall memory", NodeKind.COMPUTE, "retrieve", recall_memory, {"memory"}),
        _node("diagnose", "Diagnose", NodeKind.AGENT, "analyze", diagnose, {"diagnoses"}),
        _node(
            "propose_rebalance",
            "Propose rebalance",
            NodeKind.AGENT,
            "synthesize",
            propose_rebalance,
            {"proposal"},
        ),
        _node("compute_trades", "Compute trades", NodeKind.COMPUTE, "compute", compute_trades, {"plan"}),
        _node("policy_gate", "Policy gate", NodeKind.GATE, "verify", policy_gate, {"policy"}),
        _node("approval", "Approval", NodeKind.APPROVAL, "verify", approval, {"approval"}),
        _node(
            "save_plan", "Save plan", NodeKind.COMPUTE, "report", save_plan, {"saved_plan"}, idempotent=False
        ),
        _node("report", "Report", NodeKind.REPORT, "report", report, {"report_md", "outcome"}),
        _node("learn", "Learn", NodeKind.COMPUTE, "report", learn, {"learned"}, idempotent=False),
    ],
    edges=[
        ("recall_memory", "diagnose"),
        ("diagnose", "propose_rebalance"),
        ("save_plan", "report"),
        ("report", "learn"),
        ("learn", "END"),
    ],
    routers=[
        _router(
            "load_portfolio",
            route_after_load,
            {"ok": "compute_analytics", "empty": "report", "__fallback__": "report"},
        ),
        _router(
            "compute_analytics",
            route_after_analytics,
            {"ok": "recall_memory", "unvalued": "report", "unavailable": "report", "__fallback__": "report"},
        ),
        _router(
            "propose_rebalance",
            route_after_proposal,
            {"compute": "compute_trades", "no_change": "report", "none": "report", "__fallback__": "report"},
        ),
        _router(
            "compute_trades",
            route_after_trades,
            {"gate": "policy_gate", "failed": "report", "__fallback__": "report"},
        ),
        _router(
            "policy_gate",
            route_after_policy,
            {
                "approve": "approval",
                "retry": "propose_rebalance",
                "reject": "report",
                "__fallback__": "report",
            },
        ),
        _router(
            "approval",
            route_after_approval,
            {"save": "save_plan", "report": "report", "__fallback__": "report"},
        ),
    ],
    entry="load_portfolio",
    signature_fields=["portfolio_id", "risk_profile", "constraints", "portfolio_hash"],
)
