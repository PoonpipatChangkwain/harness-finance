"""``trading`` workflow (P10, T1-T5, F1, F3): one symbol through a TradingAgents-style desk.

    resolve_symbol → load_context → analysts (concurrent) → bull ⇄ bear (N rounds)
    → research_manager (structured) → trader (structured + compute sizing)
    → aggressive → conservative → neutral (M rounds) → portfolio_manager (structured)
    → policy_gate → refresh_price (live quote, re-size, re-check) → approval (interrupt;
    re-priced again on approve) → record_decision → report

Ported from ``../TradingAgents/tradingagents/graph/setup.py`` (node order, debate
routers, path maps) and the agents under ``tradingagents/agents/``. Differences:

- the analysts run concurrently *inside one node* (``asyncio.gather`` over the
  harness agent loop) rather than as parallel graph nodes: one checkpoint for the
  whole analysis step, no fan-in reducer races, same wall-clock benefit;
- every decision number (entry, stop, targets, R:R, units, weight) comes from the
  internal ``trade_snapshot``/``size_trade`` tools, so it has a ``kind=compute``
  provenance record; LLMs only propose a *basis* (F1);
- every structured call fails safe to ``REVIEW`` (T5), and a human approves every
  trade that passes the policy gate (T4). Nothing is ever sent to a broker.

Framework-neutral (F2): nodes only use ``NodeContext`` (llm, tools, agent, emit,
config); the harness supplies a ``DecisionLogPort`` through ``config``.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Literal, Protocol, TypedDict

from pydantic import BaseModel, Field
from ulid import ULID

from app.domain.agents.library import get_role, role_profile
from app.domain.agents.schemas import PortfolioDecision, TradeResearchPlan, TraderProposal
from app.domain.models.decision import Decision, Rating, Verdict, route_decision
from app.domain.models.horizon import INTERVAL_MINUTES, horizon
from app.domain.models.symbol import AssetType, Market, normalize_symbol, safe_path_component
from app.domain.policy import (
    CashSufficient,
    MaxPositionWeight,
    MaxRiskPerTrade,
    MinConfidence,
    MinRiskReward,
    NoHighImpactEventSoon,
    NoShortIfDisallowed,
    PolicySet,
    RequireStopLoss,
    ValidPendingOrder,
    ValidSymbol,
)
from app.domain.ports import AgentRunRequest, LLMMessage, ToolCallReq
from app.domain.tools.trading import DEFAULT_MAX_ORDER_DISTANCE_ATR
from app.domain.workflows.spec import (
    ApprovalRequest,
    ApprovalResponse,
    NodeContext,
    NodeKind,
    NodeSpec,
    RouterSpec,
    WorkflowSpec,
)

AnalystName = Literal["market", "news", "fundamentals", "sentiment", "macro"]
ANALYST_ROLES: dict[str, str] = {
    "market": "trade_market_analyst",
    "news": "trade_news_analyst",
    "fundamentals": "trade_fundamentals_analyst",
    "sentiment": "trade_sentiment_analyst",
    "macro": "trade_macro_analyst",
}
RISK_ORDER = ("aggressive", "conservative", "neutral")
RISK_ROLES = {
    "aggressive": "trade_aggressive_debator",
    "conservative": "trade_conservative_debator",
    "neutral": "trade_neutral_debator",
}
ANALYST_MAX_ITERATIONS = 6
DEFAULT_NOTIONAL = 1_000_000.0
DEFAULT_RISK_PCT = 0.01
MAX_POLICY_ATTEMPTS = 2  # the first proposal + one trader retry (phase-10 §10.2)
MAX_ENTRY_DRIFT_ATR = 0.5  # approval rejects when price moved further than this since the card was shown
DEFAULT_MIN_CONFIDENCE = 0.7  # below it the PM's call is not traded (block) unless Settings say warn
DEFAULT_MIN_RISK_REWARD = 1.5  # R:R at the first target must exceed it; 0 = rule off
# (raised from 1.0: backtests + live showed frequent stop-outs — at a realistic ~30-40% win
# rate, a 1:1 floor is a losing expectancy; 1.5 forces the trader to find a better entry/stop)
# a trader retry cannot fix these (confidence is the PM's own call; retrying invites inflating it)
NO_RETRY_RULES = frozenset({"min_confidence"})
LESSONS_SAME_SYMBOL = 3
LESSONS_OTHER_SYMBOLS = 2


# -- input / state ------------------------------------------------------------------------------


class TradeRunInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    analysis_date: date | None = None  # None = latest bar
    analysts: list[AnalystName] | None = None  # None = default for the asset type
    debate_rounds: int = Field(default=1, ge=1, le=3)
    risk_rounds: int = Field(default=1, ge=1, le=3)
    use_portfolio: bool = True
    portfolio_id: str | None = Field(default=None, max_length=40)  # (P12) None = the default book
    horizon: Literal["intraday", "swing", "position"] = "swing"
    language: Literal["th", "en"] = "th"
    # (P15) set only by the backtest runner: auto-approval, no live decision log, own threads
    backtest_id: str | None = Field(default=None, max_length=64)


class TradeRunOutput(BaseModel):
    symbol: str
    status: str  # approved | rejected | no_trade | invalid | unavailable
    rating: str | None = None
    verdict: str | None = None
    report_md: str = ""


class TradingState(TypedDict, total=False):
    input: dict[str, Any]
    instrument: dict[str, Any]
    market_snapshot: dict[str, Any]
    event_risk: dict[str, Any]
    portfolio_ctx: dict[str, Any]
    past_lessons: list[str]
    reports: dict[str, Any]
    invest_debate: dict[str, Any]
    research_plan: dict[str, Any]
    trade_proposal: dict[str, Any]
    risk_debate: dict[str, Any]
    final_decision: dict[str, Any]
    policy: dict[str, Any]
    live: dict[str, Any]
    approval: dict[str, Any]
    decision_record: dict[str, Any]
    report_md: str
    outcome: dict[str, Any]


class DecisionLogPort(Protocol):
    """Append-only decision log (port of ``TradingMemoryLog``); implemented by the harness."""

    async def append(self, record: dict[str, Any]) -> str: ...

    async def lessons(
        self, symbol: str, limit: int = 3, others: int = 0, *, before: date | None = None
    ) -> list[str]:
        """The latest ``limit`` lessons of ``symbol``, then the latest ``others`` of other symbols.

        ``before`` (a backtest's analysis date) keeps only lessons settled before that day, so
        a run dated in the past never learns from how later trades ended.
        """
        ...


def default_analysts(asset_type: AssetType) -> list[AnalystName]:
    if asset_type == AssetType.STOCK:
        return ["market", "news", "fundamentals", "sentiment"]
    if asset_type == AssetType.CRYPTO:
        return ["market", "news", "sentiment"]
    return ["market", "news", "macro"]  # forex, commodity (gold), index


def select_analysts(asset_type: AssetType, requested: list[AnalystName] | None) -> list[AnalystName]:
    chosen = list(dict.fromkeys(requested or default_analysts(asset_type)))
    if asset_type != AssetType.STOCK and "fundamentals" in chosen:
        chosen.remove("fundamentals")  # no company behind a pair/commodity: use macro instead
        if "macro" not in chosen:
            chosen.append("macro")
    return chosen or ["market"]


def lot_size_for(asset_type: AssetType, market: Market) -> float:
    if asset_type == AssetType.STOCK and market == Market.TH:
        return 100  # SET board lot
    if asset_type == AssetType.CRYPTO:
        return 0.0001
    return 1


# -- helpers ------------------------------------------------------------------------------------


async def _tool(ctx: NodeContext, name: str, args: dict[str, Any]) -> Any:
    (result,) = await ctx.tools.run_calls([ToolCallReq(id=f"call_{ULID()}", name=name, args=args)])
    return result


def _lang(state: dict[str, Any]) -> str:
    return "Thai" if state["input"].get("language", "th") == "th" else "English"


def _system(role: str, state: dict[str, Any]) -> str:
    return f"{role_profile(get_role(role))}\n\nWrite in {_lang(state)}. Keep tickers and numbers as given."


def _num_fields(values: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v:.4g}" for k, v in values.items() if isinstance(v, int | float))


def _event_text(state: dict[str, Any]) -> str:
    risk = state.get("event_risk") or {}
    if not risk.get("available"):
        why = risk.get("error") or "not loaded"
        return f"EVENT RISK: calendar unavailable ({why}) — say so, do not assume there are no events"
    events = [e for e in risk.get("events") or [] if e["impact"] in ("high", "medium", "holiday")]
    head = (
        f"EVENT RISK ({risk.get('source')}, {'/'.join(risk.get('currencies') or [])}, "
        f"next {risk.get('window_hours')}h; times UTC)"
    )
    if not events:
        return f"{head}: no high/medium-impact releases scheduled"

    def line(e: dict[str, Any]) -> str:
        text = (
            f"- {e['at'][:16]} ({e['hours_from_now']:+}h) {e['impact'].upper()} {e['currency']} {e['title']}"
        )
        if e.get("forecast") or e.get("previous"):
            text += f" (forecast {e['forecast']}, previous {e['previous']})"
        return text + (f" actual {e['actual']}" if e.get("actual") else "")

    lines = [line(e) for e in events[:15]]
    return head + ":\n" + "\n".join(lines)


def _snapshot_text(state: dict[str, Any]) -> str:
    snap = state.get("market_snapshot") or {}
    if not snap.get("available"):
        return "MARKET SNAPSHOT: unavailable"
    interval = snap.get("interval", "1d")
    text = (
        f"MARKET SNAPSHOT (ground truth, {interval} bars, as of {snap.get('as_of')}, last bar "
        f"{snap.get('bar_ts')}, {snap.get('currency')}): close={snap.get('close')}, "
        f"{_num_fields(snap.get('indicators') or {})}"
    )
    changes = _num_fields(snap.get("change_pct") or {})
    if changes:
        text += f"; change {changes}"
    if snap.get("range_high") is not None:
        text += f"; range of last 252 bars {snap.get('range_low'):.6g}-{snap.get('range_high'):.6g}"
    htf = snap.get("higher_tf")
    if htf:
        text += (
            f"\nHIGHER TIMEFRAME (daily, as of {htf.get('as_of')}): close={htf.get('close')}, "
            f"{_num_fields(htf.get('indicators') or {})}; 52w {htf.get('low_52w')}-{htf.get('high_52w')}"
        )
    elif snap.get("high_52w") is not None:
        text += f"; 52w {snap.get('low_52w')}-{snap.get('high_52w')}"
    return f"{text}\n{_event_text(state)}"


def _reports_text(state: dict[str, Any]) -> str:
    parts = []
    for name, rep in (state.get("reports") or {}).items():
        # ported from TradingAgents' report_or_absent: an absence must not read as a blank finding
        body = (rep.get("text") or "").strip() or (
            f"(No {name} report in this run ({rep.get('status')}): it is not available, "
            "not an empty finding.)"
        )
        parts.append(f"### {name} analyst\n{body}")
    return "\n\n".join(parts) or "(no analyst reports)"


def _instrument_line(state: dict[str, Any]) -> str:
    inst = state["instrument"]
    snap = state.get("market_snapshot") or {}
    spec = horizon(state["input"].get("horizon"))
    line = (
        f"Instrument: {inst['display']} ({inst['canonical']}, {inst['asset_type']}, {inst['market']}, "
        f"{inst['currency']}).\n{spec.playbook()}"
    )
    if snap.get("available") and snap.get("interval") != spec.interval:
        line += f" ({spec.bars} bars were unavailable: this run uses {snap.get('interval')} bars.)"
    return line


def _history_text(history: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"[{h['speaker']}] {h['text']}" for h in history) or "(no turns yet)"


def _decision_or_review(decision: Decision | None, reason: str) -> Decision:
    return decision or Decision(verdict=Verdict.REVIEW, confidence=0.0, reason=reason)


# -- nodes: setup -------------------------------------------------------------------------------


async def resolve_symbol(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    inp = TradeRunInput.model_validate(state["input"])
    try:
        sym = normalize_symbol(inp.symbol)
        safe_path_component(sym.canonical)  # also rejects path tricks like "../x"
    except ValueError as exc:
        return {
            "instrument": {"valid": False, "raw": inp.symbol, "error": str(exc)},
            "_summary": f"invalid symbol {inp.symbol!r}",
        }
    analysts = select_analysts(sym.asset_type, inp.analysts)
    instrument = {
        "valid": True,
        "raw": sym.raw,
        "canonical": sym.canonical,
        "display": sym.display,
        "asset_type": sym.asset_type.value,
        "market": sym.market.value,
        "currency": sym.currency,
        "lot_size": lot_size_for(sym.asset_type, sym.market),
        "analysts": analysts,
        "analysis_date": inp.analysis_date.isoformat() if inp.analysis_date else None,
    }
    return {
        "instrument": instrument,
        "_summary": f"{sym.canonical} · {sym.asset_type.value} · {', '.join(analysts)}",
    }


def route_after_resolve(state: dict[str, Any]) -> str:
    return "ok" if state.get("instrument", {}).get("valid") else "invalid"


def _snapshot_value(data: dict[str, Any], provenance_ids: list[str]) -> dict[str, Any]:
    latest = data.get("latest_bar") or {}
    return {
        "available": True,
        "interval": data.get("interval", "1d"),
        "as_of": data.get("as_of"),
        "bar_ts": latest.get("ts"),
        "currency": data.get("currency"),
        "close": latest.get("close"),
        "indicators": data.get("indicators") or {},
        "change_pct": data.get("change_pct") or {},
        "high_52w": data.get("high_52w"),
        "low_52w": data.get("low_52w"),
        "range_high": data.get("range_high_252_bars"),
        "range_low": data.get("range_low_252_bars"),
        "provenance_ids": provenance_ids,
    }


async def _load_snapshot(state: dict[str, Any], ctx: NodeContext, interval: str, purpose: str) -> Any:
    inst = state["instrument"]
    args: dict[str, Any] = {"symbol": inst["canonical"], "interval": interval, "purpose": purpose}
    if inst.get("analysis_date"):
        args["as_of"] = inst["analysis_date"]
    return await _tool(ctx, "trade_snapshot", args)


async def _event_risk(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    inst = state["instrument"]
    spec = horizon(state["input"].get("horizon"))
    if inst.get("analysis_date") and inst["analysis_date"] < date.today().isoformat():
        return {"available": False, "error": "no calendar history for a backtest date"}
    res = await _tool(
        ctx,
        "trade_event_risk",
        {"symbol": inst["canonical"], "hours_ahead": spec.event_window_h, "hours_back": 2,
         "min_impact": "medium", "purpose": "load_context"},
    )  # fmt: skip
    if not res.ok or not isinstance(res.data, dict):
        return {"available": False, "error": res.summary}
    events = res.data.get("events") or []
    upcoming_high = [e for e in events if e["impact"] == "high" and e["hours_from_now"] >= 0]
    return {
        "available": True,
        "source": "ForexFactory",
        "window_hours": spec.event_window_h,
        "currencies": res.data.get("currencies") or [],
        "events": events,
        "next_high_impact_hours": upcoming_high[0]["hours_from_now"] if upcoming_high else None,
        "provenance_ids": list(res.provenance_ids),
    }


async def load_context(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    inst = state["instrument"]
    spec = horizon(state["input"].get("horizon"))
    # the horizon decides the bars: entry, ATR (stop distance) and indicators come from them
    res = await _load_snapshot(state, ctx, spec.interval, "load_context")
    if not (res.ok and isinstance(res.data, dict)) and spec.interval != "1d":
        await ctx.emit.emit(
            "warning",
            {
                "code": "intraday_unavailable",
                "message": f"{spec.interval} bars unavailable ({res.summary}); analysing daily bars instead",
            },
        )
        res = await _load_snapshot(state, ctx, "1d", "load_context_daily")
    if res.ok and isinstance(res.data, dict):
        snapshot = _snapshot_value(res.data, list(res.provenance_ids))
        if snapshot["interval"] != "1d":  # the daily chart is the higher-timeframe context
            htf = await _load_snapshot(state, ctx, "1d", "higher_timeframe")
            if htf.ok and isinstance(htf.data, dict):
                snapshot["higher_tf"] = _snapshot_value(htf.data, list(htf.provenance_ids))
    else:
        snapshot = {"available": False, "error": res.summary}
    event_risk = await _event_risk(state, ctx) if snapshot["available"] else {"available": False}

    # (P12) the harness reads the saved book: {state, equity, cash, symbol_weight, ...} in the
    # instrument's currency; use_portfolio=false (or no book) is "not_provided", never "flat"
    portfolio_fn = ctx.config.get("portfolio") if state["input"].get("use_portfolio", True) else None
    portfolio = (
        await portfolio_fn(inst["canonical"], state["input"].get("portfolio_id"))
        if portfolio_fn
        else {"state": "not_provided"}
    )

    log: DecisionLogPort | None = ctx.config.get("decision_log")
    analysis_date = inst.get("analysis_date")
    lessons = (
        await log.lessons(
            inst["canonical"],
            LESSONS_SAME_SYMBOL,
            LESSONS_OTHER_SYMBOLS,
            before=date.fromisoformat(analysis_date) if analysis_date else None,
        )
        if log is not None
        else []
    )
    nxt = event_risk.get("next_high_impact_hours")
    return {
        "market_snapshot": snapshot,
        "event_risk": event_risk,
        "portfolio_ctx": portfolio,
        "past_lessons": lessons,
        "invest_debate": {"history": [], "count": 0, "latest_speaker": None},
        "_summary": (
            f"{snapshot.get('interval')} close {snapshot.get('close')} · "
            + (f"high-impact event in {nxt}h · " if nxt is not None else "")
            + f"portfolio {portfolio.get('state')} · {len(lessons)} lessons"
            if snapshot["available"]
            else f"snapshot unavailable: {snapshot.get('error')}"
        ),
    }


def route_after_context(state: dict[str, Any]) -> str:
    return "ok" if state.get("market_snapshot", {}).get("available") else "unavailable"


# -- nodes: analysts (concurrent) ---------------------------------------------------------------


async def analysts(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    inst = state["instrument"]
    names: list[str] = inst["analysts"]
    base = f"{_instrument_line(state)}\n{_snapshot_text(state)}"
    if inst.get("analysis_date"):
        base += f"\nAnalysis date: {inst['analysis_date']} — ignore anything published after it."

    interval = (state.get("market_snapshot") or {}).get("interval", "1d")

    async def run(name: str) -> dict[str, Any]:
        instruction = (
            f"{base}\n\nWrite your {name} analyst report for this instrument in {_lang(state)}. "
            "The snapshot above is already verified; do not fetch it again. "
            f'Analyse the {interval} bars: pass interval="{interval}" to get_prices and compute_indicators. '
            "Weigh the scheduled events above against the holding period."
        )
        result = await ctx.agent.run_loop(
            AgentRunRequest(
                role=ANALYST_ROLES[name],
                instruction=instruction,
                max_iterations=ANALYST_MAX_ITERATIONS,
                wrap_up=True,
            )
        )
        status = "ok" if result.draft else "empty"
        if result.stop_reason and result.stop_reason != "cancelled":
            status = "partial" if result.draft else "failed"
        return {
            "status": status,
            "text": result.draft,
            "tool_calls": result.tool_calls,
            "stop_reason": result.stop_reason,
        }

    outcomes = await asyncio.gather(*(run(n) for n in names), return_exceptions=True)
    reports: dict[str, Any] = {}
    for name, out in zip(names, outcomes, strict=True):
        if isinstance(out, BaseException):
            if isinstance(out, asyncio.CancelledError):
                raise out
            reports[name] = {"status": "failed", "text": "", "error": f"{type(out).__name__}: {out}"}
            await ctx.emit.emit("warning", {"code": "analyst_failed", "message": f"{name}: {out}"})
        else:
            reports[name] = out
    ok = sum(1 for r in reports.values() if r.get("text"))
    return {"reports": reports, "_summary": f"{ok}/{len(names)} reports · {', '.join(names)}"}


# -- nodes: investment debate -------------------------------------------------------------------


def _lessons_text(state: dict[str, Any]) -> str:
    """Lessons from settled trades (this symbol first, then others), incl. the trader's own notes."""
    lessons = state.get("past_lessons") or []
    if not lessons:
        return ""
    return (
        "## Lessons from past trades (this symbol first, then other instruments; "
        "a trader's note is their own rule — respect it)\n" + "\n".join(f"- {x}" for x in lessons) + "\n\n"
    )


async def _debate_turn(
    state: dict[str, Any], ctx: NodeContext, side: Literal["bull", "bear"]
) -> dict[str, Any]:
    debate = dict(state.get("invest_debate") or {"history": [], "count": 0})
    history: list[dict[str, Any]] = list(debate.get("history") or [])
    opponent = "bear" if side == "bull" else "bull"
    last_opponent = next((h["text"] for h in reversed(history) if h["speaker"] == opponent), "")
    user = (
        f"{_instrument_line(state)}\n{_snapshot_text(state)}\n\n"
        f"## Analyst reports\n{_reports_text(state)}\n\n"
        f"## Debate so far\n{_history_text(history)}\n\n"
        + _lessons_text(state)
        + (
            f"Rebut the {opponent}'s last argument:\n{last_opponent}\n\n"
            if last_opponent
            else f"(The {opponent} analyst has not spoken yet — open the debate with your own case.)\n\n"
        )
        + f"Give your {side} argument now."
    )
    messages = [
        LLMMessage(role="system", content=_system(f"trade_{side}_researcher", state)),
        LLMMessage(role="user", content=user),
    ]
    try:
        resp = await ctx.llm.complete(messages, tier="deep", purpose="debate")
        text = resp.text.strip() or "(no argument)"
    except Exception as exc:
        text = f"(argument unavailable: {type(exc).__name__})"
        await ctx.emit.emit("warning", {"code": "llm_error", "message": f"{side}: {exc}"})
    history.append({"speaker": side, "text": text})
    count = int(debate.get("count") or 0) + 1
    return {
        "invest_debate": {**debate, "history": history, "count": count, "latest_speaker": side},
        "_summary": f"{side} turn {count}",
    }


async def bull_researcher(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    return await _debate_turn(state, ctx, "bull")


async def bear_researcher(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    return await _debate_turn(state, ctx, "bear")


def should_continue_debate(state: dict[str, Any]) -> str:
    """Port of ``ConditionalLogic.should_continue_debate``: 2 turns per round, bull ↔ bear."""
    debate = state.get("invest_debate") or {}
    rounds = int(state["input"].get("debate_rounds", 1))
    if int(debate.get("count") or 0) >= 2 * rounds:
        return "manager"
    speaker = debate.get("latest_speaker")
    if speaker == "bull":
        return "bear"
    if speaker == "bear":
        return "bull"
    return "__fallback__"


async def research_manager(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    debate = state.get("invest_debate") or {}
    user = (
        f"{_instrument_line(state)}\n{_snapshot_text(state)}\n\n"
        f"## Analyst reports\n{_reports_text(state)}\n\n"
        f"## Bull vs bear debate\n{_history_text(debate.get('history') or [])}\n\n"
        "Judge the debate and return the research plan."
    )
    messages = [
        LLMMessage(role="system", content=_system("trade_research_manager", state)),
        LLMMessage(role="user", content=user),
    ]
    result = await ctx.llm.structured(messages, TradeResearchPlan, tier="deep", purpose="structured")
    plan = result.value
    decision = _decision_or_review(plan.decision if plan else None, "research manager output unparseable")
    verdict = route_decision(decision)
    value = {
        "stance": (plan.stance if plan else Rating.HOLD).value,
        "thesis": plan.thesis if plan else "",
        "key_risks": plan.key_risks if plan else [],
        "catalysts": plan.catalysts if plan else [],
        "decision": decision.model_dump(mode="json"),
        "verdict": verdict.value,
        "mode": result.mode,
    }
    return {
        "research_plan": value,
        "_summary": f"{value['stance']} · {verdict.value} ({decision.confidence:.2f})",
    }


def route_after_research(state: dict[str, Any]) -> str:
    return "reject" if state["research_plan"]["verdict"] == Verdict.REJECT.value else "trade"


# -- nodes: trader + sizing ---------------------------------------------------------------------


def _equity(state: dict[str, Any], ctx: NodeContext) -> float:
    pf = state.get("portfolio_ctx") or {}
    equity = pf.get("equity")
    if pf.get("state") in ("positions", "flat") and isinstance(equity, int | float) and equity > 0:
        return float(equity)
    return float(ctx.config.get("default_notional", DEFAULT_NOTIONAL))


async def _size(
    state: dict[str, Any],
    ctx: NodeContext,
    proposal: dict[str, Any],
    *,
    attempt: str,
    size_fraction: float = 1.0,
    quote: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Compute sizing from a basis; entry is the live ``quote`` when given, else the snapshot close."""
    inst, snap = state["instrument"], state["market_snapshot"]
    stock = inst["asset_type"] == AssetType.STOCK.value
    inputs = [*(snap.get("provenance_ids") or []), *((quote or {}).get("provenance_ids") or [])]
    spec = horizon(state["input"].get("horizon"))
    interval = snap.get("interval") or spec.interval
    bars = proposal.get("time_stop_bars") or spec.time_stop_bars
    if interval != spec.interval:  # fell back to daily bars: keep the horizon's wall-clock time stop
        bars = max(1, round(bars * INTERVAL_MINUTES[spec.interval] / INTERVAL_MINUTES[interval]))
    args = {
        "symbol": inst["canonical"],
        "side": proposal["side"],
        "entry": quote["price"] if quote else snap["close"],
        "order_type": proposal.get("order_type") or "market",
        "order_price": proposal.get("order_price"),
        "order_valid_bars": proposal.get("order_valid_bars"),
        "max_order_distance_atr": float(
            ctx.config.get("max_order_distance_atr", DEFAULT_MAX_ORDER_DISTANCE_ATR)
        ),
        "stop_basis": proposal.get("stop_basis") or "atr",
        "atr": (snap.get("indicators") or {}).get("atr14"),
        "atr_multiple": proposal.get("atr_multiple"),
        "stop_level": proposal.get("stop_level"),
        "targets": proposal.get("target_levels") or [],
        "equity": _equity(state, ctx),
        "risk_pct": float(ctx.config.get("risk_pct", DEFAULT_RISK_PCT)),
        "size_fraction": size_fraction,
        "lot_size": inst.get("lot_size", 1),
        "max_position_weight": float(ctx.config.get("max_position_weight", 0.20)) if stock else None,
        "currency": inst["currency"],
        "time_stop_bars": bars,
        "interval": interval,
        "start": (quote or {}).get("ts") or snap.get("bar_ts"),
        "attempt": attempt,
        "input_provenance_ids": inputs,
    }
    res = await _tool(ctx, "size_trade", args)
    if not res.ok or not isinstance(res.data, dict):
        return {"side": proposal["side"], "stop": None, "units": 0, "notes": [res.summary]}, list(
            res.provenance_ids
        )
    return dict(res.data), list(res.provenance_ids)


async def trader(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    plan = state["research_plan"]
    previous = state.get("policy") or {}
    failures = [
        r for r in previous.get("results", []) if not r.get("passed") and r.get("severity") == "block"
    ]
    attempt = int(previous.get("attempts") or 0) + 1
    user = (
        f"{_instrument_line(state)}\n{_snapshot_text(state)}\n\n## Research plan\n"
        f"stance={plan['stance']}; thesis={plan['thesis']}\n"
        f"risks={plan['key_risks']}\ncatalysts={plan['catalysts']}\n\n"
        f"## Analyst reports\n{_reports_text(state)}\n\n"
        f"## Portfolio context\n{_portfolio_text(state.get('portfolio_ctx') or {})}\n\n"
        + _lessons_text(state)
        + _limits_text(ctx)
        + (
            "## Your previous proposal was blocked by the risk policy — fix it:\n"
            + "\n".join(f"- {f['rule_id']}: {f['message']}" for f in failures)
            + "\n\n"
            if failures
            else ""
        )
        + "Return your trade proposal (basis only; the system computes stop price and size)."
    )
    messages = [
        LLMMessage(role="system", content=_system("trade_trader", state)),
        LLMMessage(role="user", content=user),
    ]
    result = await ctx.llm.structured(messages, TraderProposal, tier="deep", purpose="structured")
    proposal_model = result.value
    if proposal_model is None:
        proposal = {
            "side": "flat",
            "entry_zone": [],
            "order_type": "market",
            "order_price": None,
            "order_valid_bars": None,
            "stop_basis": "atr",
            "atr_multiple": None,
            "stop_level": None,
            "target_levels": [],
            "horizon": state["input"].get("horizon", "swing"),
            "time_stop_bars": None,
            "invalidation": "",
            "rationale": "",
            "decision": _decision_or_review(None, "trader output unparseable").model_dump(mode="json"),
        }
    else:
        proposal = proposal_model.model_dump(mode="json")
    llm_units = proposal.pop("proposed_units", None)
    sizing, prov = await _size(state, ctx, proposal, attempt=f"trader#{attempt}")
    if llm_units is not None and llm_units != sizing.get("units"):
        await ctx.emit.emit(
            "warning",
            {
                "code": "llm_size_overwritten",
                "message": f"trader proposed {llm_units} units; compute sized {sizing.get('units')}",
            },
        )
    value = {
        "proposal": proposal,
        "sizing": sizing,
        "provenance_ids": prov,
        "mode": result.mode,
        "attempt": attempt,
    }
    stop = sizing.get("stop")
    return {
        "trade_proposal": value,
        "risk_debate": {"history": [], "count": 0, "latest_speaker": None},
        "_summary": f"{proposal['side']} · stop {stop if stop is not None else 'none'} · "
        f"{sizing.get('units')} units",
    }


def _proposal_text(state: dict[str, Any]) -> str:
    tp = state["trade_proposal"]
    p, s = tp["proposal"], tp["sizing"]
    order = s.get("order") or "market"
    how = (
        f"entry={s.get('entry')} (market)"
        if order == "market"
        else f"PENDING {order.replace('_', ' ')} at {s.get('entry')} (current price {s.get('market_price')}, "
        f"{s.get('order_distance_atr')} ATR away, expires after {s.get('order_valid_bars')} bars; "
        f"not filled = no trade)"
    )
    return (
        f"side={p['side']}, {how}, stop={s.get('stop')}, targets={s.get('targets')}, "
        f"risk_reward={s.get('risk_reward')}, units={s.get('units')}, notional={s.get('notional')}, "
        f"weight={s.get('position_weight')}, risk_fraction={s.get('risk_fraction')}, "
        f"time stop={s.get('time_stop_bars')} x {s.get('interval')} bars (at {s.get('time_stop_at')}) "
        f"(all computed)\ninvalidation: {p.get('invalidation') or '(none given)'}\n"
        f"rationale: {p.get('rationale')}"
    )


# -- nodes: risk debate -------------------------------------------------------------------------


async def _risk_turn(state: dict[str, Any], ctx: NodeContext, who: str) -> dict[str, Any]:
    debate = dict(state.get("risk_debate") or {"history": [], "count": 0})
    history: list[dict[str, Any]] = list(debate.get("history") or [])
    user = (
        f"{_instrument_line(state)}\n{_snapshot_text(state)}\n\n"
        f"## Trade proposal\n{_proposal_text(state)}\n\n"
        f"## Risk debate so far\n{_history_text(history)}\n\nGive your {who} view now."
    )
    messages = [
        LLMMessage(role="system", content=_system(RISK_ROLES[who], state)),
        LLMMessage(role="user", content=user),
    ]
    try:
        resp = await ctx.llm.complete(messages, tier="quick", purpose="debate")
        text = resp.text.strip() or "(no view)"
    except Exception as exc:
        text = f"(view unavailable: {type(exc).__name__})"
        await ctx.emit.emit("warning", {"code": "llm_error", "message": f"{who}: {exc}"})
    history.append({"speaker": who, "text": text})
    count = int(debate.get("count") or 0) + 1
    return {
        "risk_debate": {**debate, "history": history, "count": count, "latest_speaker": who},
        "_summary": f"{who} turn {count}",
    }


async def aggressive(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    return await _risk_turn(state, ctx, "aggressive")


async def conservative(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    return await _risk_turn(state, ctx, "conservative")


async def neutral(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    return await _risk_turn(state, ctx, "neutral")


def should_continue_risk(state: dict[str, Any]) -> str:
    """Port of ``should_continue_risk_analysis``: 3 turns per round, aggressive → conservative → neutral."""
    debate = state.get("risk_debate") or {}
    rounds = int(state["input"].get("risk_rounds", 1))
    if int(debate.get("count") or 0) >= 3 * rounds:
        return "pm"
    speaker = debate.get("latest_speaker")
    if speaker in RISK_ORDER:
        return RISK_ORDER[(RISK_ORDER.index(speaker) + 1) % len(RISK_ORDER)]
    return "__fallback__"


def _portfolio_text(pf: dict[str, Any]) -> str:
    state = pf.get("state")
    if state == "not_provided":
        return "not provided (the user's holdings are unknown — do not assume an empty book)"
    parts = [f"state={state}"]
    for key in ("equity", "cash"):
        value = pf.get(key)
        if isinstance(value, int | float):
            parts.append(f"{key}={value:.4g}")
    weight = pf.get("symbol_weight")
    if isinstance(weight, int | float):
        parts.append(f"current weight of this symbol={weight:.2%}")
    if pf.get("currency"):
        parts.append(f"currency={pf['currency']}")
    return ", ".join(parts)


async def portfolio_manager(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    risk = state.get("risk_debate") or {}
    pf = state.get("portfolio_ctx") or {}
    user = (
        f"{_instrument_line(state)}\n{_snapshot_text(state)}\n\n## Research plan\n"
        f"{state['research_plan']['stance']}: {state['research_plan']['thesis']}\n\n"
        f"## Trade proposal\n{_proposal_text(state)}\n\n"
        + _lessons_text(state)
        + _limits_text(ctx)
        + f"## Risk debate\n{_history_text(risk.get('history') or [])}\n\n"
        f"## Portfolio context\n{_portfolio_text(pf)}\n\nReturn your final decision."
    )
    messages = [
        LLMMessage(role="system", content=_system("trade_portfolio_manager", state)),
        LLMMessage(role="user", content=user),
    ]
    result = await ctx.llm.structured(messages, PortfolioDecision, tier="deep", purpose="structured")
    pm = result.value
    decision = _decision_or_review(pm.decision if pm else None, "portfolio manager output unparseable")
    verdict = route_decision(decision)
    fraction = pm.final_size_pct if pm else 1.0
    proposal = state["trade_proposal"]["proposal"]
    sizing, prov = await _size(
        state,
        ctx,
        proposal,
        attempt=f"pm#{state['trade_proposal'].get('attempt', 1)}",
        size_fraction=fraction,
    )
    value = {
        "rating": (pm.rating if pm else Rating.HOLD).value,
        "decision": decision.model_dump(mode="json"),
        "verdict": verdict.value,
        "final_size_pct": fraction,
        "adjustments": pm.adjustments if pm else "",
        "summary": pm.summary if pm else "",
        "sizing": sizing,
        "provenance_ids": prov,
        "mode": result.mode,
    }
    return {
        "final_decision": value,
        "_summary": f"{value['rating']} · {verdict.value} ({decision.confidence:.2f}) · "
        f"{sizing.get('units')} units",
    }


def route_after_pm(state: dict[str, Any]) -> str:
    return "reject" if state["final_decision"]["verdict"] == Verdict.REJECT.value else "gate"


# -- nodes: policy gate + approval + record + report --------------------------------------------


def _min_risk_reward(config: Any) -> float:
    return float(config.get("min_risk_reward", DEFAULT_MIN_RISK_REWARD))


def _limits_text(ctx: NodeContext) -> str:
    """Gate limits the plan is built against. The confidence floor is left out on purpose: a model
    told the bar tends to report a confidence just above it."""
    rr = _min_risk_reward(ctx.config)
    reach = float(ctx.config.get("max_order_distance_atr", DEFAULT_MAX_ORDER_DISTANCE_ATR))
    lines = [
        f"- A limit/stop entry must be on the right side of the current price and within {reach:g} x ATR "
        "of it; R:R is then measured from the order price.\n"
    ]
    if rr > 0:
        lines.insert(
            0,
            f"- R:R at the FIRST target must exceed {rr:g}; a plan without a target beyond entry is "
            "blocked. Place the first target accordingly, wait for a better entry with a limit order, "
            "or stay flat.\n",
        )
    return "## Risk policy (enforced by code after you)\n" + "".join(lines) + "\n"


def trading_policy(asset_type: str, config: Any, horizon_name: str | None = None) -> PolicySet:
    """Rule set of the gate; ``horizon_name`` (live runs only) adds the event-risk guard."""
    min_rr = _min_risk_reward(config)
    rules: list[Any] = [
        ValidSymbol(),
        ValidPendingOrder(),
        *([RequireStopLoss()] if config.get("require_stop_loss", True) else []),
        *([MinRiskReward(min_rr)] if min_rr > 0 else []),
        MaxRiskPerTrade(float(config.get("max_risk_per_trade", 0.02))),
        MinConfidence(
            float(config.get("min_confidence", DEFAULT_MIN_CONFIDENCE)),
            severity="block" if config.get("block_on_low_confidence", True) else "warn",
        ),
        NoShortIfDisallowed(),
    ]
    if horizon_name is not None:
        guard = float(horizon(horizon_name).event_guard_h)
        rules.append(
            NoHighImpactEventSoon(guard, severity="block" if config.get("block_on_event_risk") else "warn")
        )
    if asset_type == AssetType.STOCK.value:  # un-levered cash equities only
        rules += [MaxPositionWeight(float(config.get("max_position_weight", 0.20))), CashSufficient()]
    return PolicySet(rules)


def _state_policy(state: dict[str, Any], ctx: NodeContext) -> PolicySet:
    backtest = bool(state["instrument"].get("analysis_date"))
    return trading_policy(
        state["instrument"]["asset_type"],
        ctx.config,
        None if backtest else state["input"].get("horizon", "swing"),
    )


def _policy_inputs(
    state: dict[str, Any], sizing: dict[str, Any], confidence: float, ctx: NodeContext
) -> tuple[dict[str, Any], dict[str, Any]]:
    inst = state["instrument"]
    side = sizing.get("side")
    action = {"long": "buy", "short": "open_short"}.get(str(side), "hold")
    units = sizing.get("units") or 0
    pf = state.get("portfolio_ctx") or {}
    existing = float(pf.get("symbol_weight") or 0.0)
    equity = _equity(state, ctx)
    proposal = {
        "symbol_valid": bool(inst.get("valid")),
        "action": action,
        "order": sizing.get("order") or "market",
        "order_error": sizing.get("order_error"),
        "order_distance_atr": sizing.get("order_distance_atr"),
        "stop_loss": sizing.get("stop"),
        "risk_reward": (sizing.get("risk_reward") or [None])[0],  # first target (computed)
        "risk_amount": sizing.get("risk_amount"),
        "position_weight": existing + float(sizing.get("position_weight") or 0.0),
        "cash_required": float(sizing.get("notional") or 0.0) if side == "long" else 0.0,
        "confidence": confidence,
        "quantity": -units if side == "short" else units,
        "event_calendar_available": bool((state.get("event_risk") or {}).get("available")),
        "hours_to_next_high_impact": (state.get("event_risk") or {}).get("next_high_impact_hours"),
    }
    cash = pf.get("cash")
    context = {
        "equity": equity,
        "cash_available": float(cash) if isinstance(cash, int | float) else equity,
        "allow_short": inst["asset_type"] != AssetType.STOCK.value
        or bool(ctx.config.get("allow_short_stocks")),
    }
    return proposal, context


async def policy_gate(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    final = state["final_decision"]
    proposal, context = _policy_inputs(state, final["sizing"], final["decision"]["confidence"], ctx)
    evaluation = _state_policy(state, ctx).evaluate(proposal, context)
    attempts = int((state.get("policy") or {}).get("attempts") or 0) + 1
    value = {
        "blocked": evaluation.blocked,
        "results": [r.model_dump(mode="json") for r in evaluation.results],
        "attempts": attempts,
    }
    failed = [r.rule_id for r in evaluation.failed]
    return {
        "policy": value,
        "_summary": ("blocked: " if evaluation.blocked else "passed") + ", ".join(failed),
    }


def route_after_policy(state: dict[str, Any]) -> str:
    policy = state["policy"]
    if not policy["blocked"]:
        return "approve"
    blocking = {r["rule_id"] for r in policy["results"] if not r["passed"] and r["severity"] == "block"}
    if blocking & NO_RETRY_RULES:
        return "reject"
    return "retry" if int(policy["attempts"]) < MAX_POLICY_ATTEMPTS else "reject"


async def _live_quote(state: dict[str, Any], ctx: NodeContext, purpose: str) -> dict[str, Any] | None:
    """Live price for the entry, or None for a backtest (the snapshot *is* that day's price)
    or when no quote is available (the caller keeps the price it already has)."""
    inst = state["instrument"]
    if inst.get("analysis_date"):
        return None
    res = await _tool(ctx, "trade_quote", {"symbol": inst["canonical"], "purpose": purpose})
    price = res.data.get("price") if res.ok and isinstance(res.data, dict) else None
    if price is None:
        await ctx.emit.emit(
            "warning",
            {
                "code": "live_price_unavailable",
                "message": f"{purpose}: {res.summary}; keeping the last price",
            },
        )
        return None
    return {
        "price": float(price),
        "ts": res.data.get("ts"),
        "provider": res.data.get("provider"),
        "provenance_ids": list(res.provenance_ids),
    }


def _evaluate(state: dict[str, Any], ctx: NodeContext, sizing: dict[str, Any]) -> dict[str, Any]:
    confidence = state["final_decision"]["decision"]["confidence"]
    proposal, context = _policy_inputs(state, sizing, confidence, ctx)
    evaluation = _state_policy(state, ctx).evaluate(proposal, context)
    return {
        "blocked": evaluation.blocked,
        "results": [r.model_dump(mode="json") for r in evaluation.results],
        "blocking": [r.rule_id for r in evaluation.blocking_failures],
    }


async def refresh_price(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    """Re-price the plan at the live quote before a human sees it: the pipeline takes minutes.

    The trader's *basis* is kept — an ATR stop moves with the entry, a stop/target level stays
    where it was (and drops out if price already crossed it) — then compute + policy run again.
    """
    final = state["final_decision"]
    quote = await _live_quote(state, ctx, "refresh")
    if quote is None:
        return {"live": {"refreshed": False}, "_summary": "snapshot price kept"}
    sizing, prov = await _size(
        state,
        ctx,
        state["trade_proposal"]["proposal"],
        attempt="refresh",
        size_fraction=final["final_size_pct"],
        quote=quote,
    )
    policy = _evaluate(state, ctx, sizing)
    close = state["market_snapshot"].get("close")
    value = {
        "refreshed": True,
        "quote": quote,
        "snapshot_close": close,
        "sizing": sizing,
        "provenance_ids": prov,
        "policy": policy,
    }
    verdict = "blocked: " + ", ".join(policy["blocking"]) if policy["blocked"] else "passed"
    return {"live": value, "_summary": f"live {quote['price']:.6g} (snapshot {close}) · {verdict}"}


def route_after_refresh(state: dict[str, Any]) -> str:
    live = state.get("live") or {}
    return "reject" if live.get("refreshed") and live["policy"]["blocked"] else "approve"


def _current(state: dict[str, Any]) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """(sizing, provenance ids, policy) of the plan as it stands before approval."""
    live = state.get("live") or {}
    if live.get("refreshed"):
        return live["sizing"], list(live["provenance_ids"]), live["policy"]
    final = state["final_decision"]
    return final["sizing"], list(final.get("provenance_ids") or []), state.get("policy") or {}


def decision_card(
    state: dict[str, Any], sizing: dict[str, Any] | None = None, quote: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The numbers a human sees; ``sizing``/``quote`` override the pre-approval plan (after approval)."""
    final = state["final_decision"]
    s = sizing or _current(state)[0]
    live = state.get("live") or {}
    quote = quote or (live.get("quote") if live.get("refreshed") else None)
    return {
        "price_source": "live" if quote else "snapshot",
        "price_ts": quote["ts"] if quote else state["market_snapshot"].get("as_of"),
        "snapshot_close": state["market_snapshot"].get("close"),
        "symbol": state["instrument"]["canonical"],
        "display": state["instrument"]["display"],
        "rating": final["rating"],
        "verdict": final["verdict"],
        "confidence": final["decision"]["confidence"],
        "side": s.get("side"),
        "entry": s.get("entry"),
        "order_type": s.get("order_type") or "market",
        "order": s.get("order") or "market",
        "market_price": s.get("market_price", s.get("entry")),
        "order_distance_atr": s.get("order_distance_atr"),
        "order_valid_bars": s.get("order_valid_bars"),
        "order_expires_at": s.get("order_expires_at"),
        "time_stop_from": s.get("time_stop_from") or "entry",
        "stop": s.get("stop"),
        "targets": s.get("targets"),
        "risk_reward": s.get("risk_reward"),
        "units": s.get("units"),
        "notional": s.get("notional"),
        "position_weight": s.get("position_weight"),
        "risk_fraction": s.get("risk_fraction"),
        "currency": s.get("currency"),
        "horizon": state["input"].get("horizon", "swing"),
        "interval": s.get("interval"),
        "time_stop_bars": s.get("time_stop_bars"),
        "time_stop_at": s.get("time_stop_at"),
        "invalidation": (state.get("trade_proposal") or {}).get("proposal", {}).get("invalidation") or "",
    }


MAX_EDIT_TARGETS = 5


def _edited_basis(
    edits: dict[str, Any], final: dict[str, Any], proposal: dict[str, Any]
) -> tuple[float, dict[str, Any]]:
    """Approval edits → (size fraction, trader proposal with the edited basis). Raises on bad input."""
    fraction = min(max(float(edits.get("final_size_pct", final["final_size_pct"])), 0.0), 1.0)
    basis = dict(proposal)
    if edits.get("stop_level") is not None:
        basis.update(stop_basis="level", stop_level=float(edits["stop_level"]))
    if edits.get("order_price") is not None:
        if (basis.get("order_type") or "market") == "market":
            raise ValueError("order_price can only be edited on a limit/stop order")
        basis["order_price"] = float(edits["order_price"])
    if edits.get("target_levels") is not None:
        targets = edits["target_levels"]
        if not isinstance(targets, list) or len(targets) > MAX_EDIT_TARGETS:
            raise ValueError(f"target_levels must be a list of at most {MAX_EDIT_TARGETS} prices")
        basis["target_levels"] = [float(t) for t in targets]
    return fraction, basis


def _auto_approval(final: dict[str, Any]) -> ApprovalResponse:
    """(P15) A backtest has no human: the PM's APPROVE is taken as is, anything else is skipped.

    The policy gate before this node and the re-check below still apply, so a backtest trades
    only what a human could have approved.
    """
    if str(final.get("verdict")) == Verdict.APPROVE.value:
        return ApprovalResponse(outcome="approved", comment="auto-approved (backtest)")
    return ApprovalResponse(
        outcome="rejected", comment=f"backtest: verdict {final.get('verdict')} is not traded"
    )


async def approval(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    final = state["final_decision"]
    card = decision_card(state)
    _, shown_prov, shown_policy = _current(state)
    if ctx.config.get("auto_approve"):
        response = _auto_approval(final)
    else:
        response = await ctx.request_approval(
            ApprovalRequest(
                kind="trade_decision",
                title=f"{card['display']}: {card['rating']} · {card['side']}",
                proposal=card,
                policy_results=shown_policy["results"],
                provenance_ids=shown_prov,
            )
        )
    value: dict[str, Any] = {
        "outcome": response.outcome,
        "comment": response.comment,
        "edits": response.edits,
    }
    if response.outcome not in ("approved", "edited"):
        return {"approval": value, "_summary": value["outcome"]}

    # The human edits the *basis* only (size ratio, stop level, target levels); every number is
    # re-derived by compute at the price of the moment of approval and the policy runs again (T4).
    basis, fraction = state["trade_proposal"]["proposal"], float(final["final_size_pct"])
    if response.outcome == "edited":
        try:
            fraction, basis = _edited_basis(response.edits or {}, final, basis)
        except (TypeError, ValueError) as exc:
            value.update(outcome="rejected", comment=f"invalid edit: {exc}")
            return {"approval": value, "_summary": "rejected"}
    fresh = await _live_quote(state, ctx, "approval")
    live = state.get("live") or {}
    quote = fresh or (live.get("quote") if live.get("refreshed") else None)
    sizing, prov = await _size(
        state, ctx, basis, attempt=f"approval#{response.outcome}", size_fraction=fraction, quote=quote
    )
    policy = _evaluate(state, ctx, sizing)
    value.update(sizing=sizing, provenance_ids=prov, final_size_pct=fraction, policy=policy, quote=quote)

    # a plan approved minutes/hours ago at another price is not the plan the human saw; a pending
    # order waits at its own price, so it is re-checked by ``valid_pending_order`` instead
    atr = (state["market_snapshot"].get("indicators") or {}).get("atr14")
    tolerance = float(ctx.config.get("max_entry_drift_atr", MAX_ENTRY_DRIFT_ATR))
    market = card["order"] == "market"
    if (
        market
        and fresh
        and isinstance(atr, int | float)
        and atr > 0
        and isinstance(card["entry"], int | float)
    ):
        drift = abs(fresh["price"] - card["entry"])
        value["entry_drift_atr"] = round(drift / atr, 4)
        if drift > tolerance * atr:
            value["outcome"] = "rejected"
            value["comment"] = (
                f"price moved {card['entry']:.6g} → {fresh['price']:.6g} ({drift / atr:.2f} ATR > "
                f"{tolerance:g} ATR) since the plan was shown; re-run for a fresh plan"
            )
            return {"approval": value, "_summary": "rejected (price moved)"}
    if policy["blocked"]:
        value["outcome"] = "rejected"
        lead = (
            "edit blocked by policy: " if response.outcome == "edited" else "blocked by policy at approval: "
        )
        value["comment"] = lead + ", ".join(policy["blocking"])
    return {"approval": value, "_summary": value["outcome"]}


def route_after_approval(state: dict[str, Any]) -> str:
    return "record" if state["approval"]["outcome"] in ("approved", "edited") else "report"


async def record_decision(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    appr = state["approval"]
    card = decision_card(state, appr.get("sizing"), appr.get("quote"))
    record = {
        "run_id": ctx.run_id,
        "symbol": card["symbol"],
        "date": state["market_snapshot"].get("as_of"),
        "card": card,
        "thesis": state["research_plan"].get("thesis"),
        "language": state["input"].get("language", "th"),
        "approval": appr["outcome"],
        "comment": appr.get("comment"),
        "provenance_ids": appr.get("provenance_ids") or _current(state)[1],
    }
    log: DecisionLogPort | None = ctx.config.get("decision_log")
    key = await log.append(record) if log is not None else f"{card['symbol']}:{ctx.run_id}"
    return {"decision_record": {"key": key, **record}, "_summary": f"recorded {key} (no order sent)"}


def _fmt(x: Any, digits: int = 4) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:,.{digits}g}" if abs(x) < 1e6 else f"{x:,.0f}"
    if isinstance(x, list):
        return ", ".join(_fmt(v) for v in x) or "-"
    return str(x)


def _outcome(state: dict[str, Any]) -> dict[str, Any]:
    inst = state.get("instrument") or {}
    if not inst.get("valid"):
        return {"status": "invalid", "reason": inst.get("error")}
    if not (state.get("market_snapshot") or {}).get("available"):
        return {"status": "unavailable", "reason": state["market_snapshot"].get("error")}
    plan = state.get("research_plan") or {}
    if plan.get("verdict") == Verdict.REJECT.value:
        return {"status": "no_trade", "reason": "research manager: " + plan["decision"]["reason"]}
    final = state.get("final_decision") or {}
    if final.get("verdict") == Verdict.REJECT.value:
        return {"status": "no_trade", "reason": "portfolio manager: " + final["decision"]["reason"]}
    policy = state.get("policy") or {}
    if policy.get("blocked"):
        blocked = [r["rule_id"] for r in policy["results"] if not r["passed"] and r["severity"] == "block"]
        return {"status": "rejected", "reason": "policy blocked: " + ", ".join(blocked)}
    live = state.get("live") or {}
    if live.get("refreshed") and live["policy"]["blocked"]:
        return {
            "status": "rejected",
            "reason": f"policy blocked at live price {live['quote']['price']:.6g}: "
            + ", ".join(live["policy"]["blocking"]),
        }
    appr = state.get("approval") or {}
    if appr.get("outcome") in ("approved", "edited"):
        return {"status": "approved", "reason": appr.get("comment")}
    return {"status": "rejected", "reason": appr.get("comment") or "rejected by reviewer"}


def _mark(r: dict[str, Any]) -> str:
    if r["passed"]:
        return "✓"
    return "✗" if r["severity"] == "block" else "!"


def render_numbers(state: dict[str, Any]) -> str:
    if not state.get("final_decision"):
        return ""
    appr = state.get("approval") or {}
    card = decision_card(state, appr.get("sizing"), appr.get("quote"))
    entry = _fmt(card["entry"])
    if card["order"] != "market":
        entry = (
            f"{card['order'].replace('_', ' ').upper()} {entry} (price now {_fmt(card['market_price'])}, "
            f"{_fmt(card['order_distance_atr'], 3)} ATR away; expires {card['order_expires_at'] or '-'})"
        )
    elif card["price_source"] == "live":
        entry += f" (live {card['price_ts'] or ''}; snapshot close {_fmt(card['snapshot_close'])})"
    rows = [
        ("Rating", card["rating"]),
        ("Verdict", f"{card['verdict']} ({card['confidence']:.2f})"),
        ("Side", card["side"]),
        ("Entry", entry),
        ("Stop", _fmt(card["stop"])),
        ("Targets", _fmt(card["targets"])),
        ("Risk/Reward", _fmt(card["risk_reward"], 3)),
        ("Units", _fmt(card["units"])),
        ("Notional", f"{_fmt(card['notional'])} {card['currency']}"),
        ("Weight", f"{(card['position_weight'] or 0):.2%}"),
        ("Risk of equity", f"{(card['risk_fraction'] or 0):.2%}"),
        ("Horizon", f"{card['horizon']} ({card['interval']} bars)"),
        (
            "Time stop",
            f"{_fmt(card['time_stop_bars'])} bars · "
            + ("from the fill" if card["time_stop_from"] == "fill" else card["time_stop_at"] or "-"),
        ),
        ("Invalidation", card["invalidation"] or "-"),
    ]
    table = "| | |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)
    # report the latest evaluation: at approval, else at the live price, else the gate's
    policy = appr.get("policy") or ((state.get("live") or {}).get("policy")) or state.get("policy") or {}
    checks = "\n".join(f"- {_mark(r)} {r['rule_id']}: {r['message']}" for r in policy.get("results", []))
    return f"## Decision (computed)\n\n{table}\n\n## Policy\n\n{checks or '(not evaluated)'}"


async def report(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    outcome = _outcome(state)
    inst = state.get("instrument") or {}
    title = (
        f"# {inst.get('display') or inst.get('raw') or state['input'].get('symbol')} — {outcome['status']}"
    )
    numbers = render_numbers(state)
    narrative = ""
    if inst.get("valid") and (state.get("market_snapshot") or {}).get("available"):
        user = (
            f"Outcome: {outcome['status']} ({outcome.get('reason') or ''}).\n{_instrument_line(state)}\n"
            f"{_snapshot_text(state)}\n\n## Analyst reports\n{_reports_text(state)}\n\n"
            f"## Research plan\n{state.get('research_plan')}\n\n{numbers}\n\n"
            "Write the narrative summary (numbers table is appended separately; do not restate it)."
        )
        messages = [
            LLMMessage(role="system", content=_system("trade_report_writer", state)),
            LLMMessage(role="user", content=user),
        ]
        try:
            narrative = (await ctx.llm.complete(messages, tier="quick", purpose="final")).text.strip()
        except Exception as exc:
            await ctx.emit.emit("warning", {"code": "final_answer_failed", "message": str(exc)})
    reason = f"\n\n> {outcome.get('reason')}" if outcome.get("reason") else ""
    disclaimer = "\n\n---\n_Analysis only. No order was sent to any broker._"
    markdown = f"{title}{reason}\n\n{narrative}\n\n{numbers}{disclaimer}".replace("\n\n\n", "\n\n").strip()
    await ctx.emit.emit("answer.final", {"markdown": markdown, "citations": [], "unverified_numbers": []})
    final = state.get("final_decision") or {}
    outcome.update(rating=final.get("rating"), verdict=final.get("verdict"))
    return {"report_md": markdown, "outcome": outcome, "_summary": outcome["status"]}


def output_of(state: dict[str, Any]) -> TradeRunOutput:
    outcome = state.get("outcome") or {}
    return TradeRunOutput(
        symbol=(state.get("instrument") or {}).get("canonical") or state["input"].get("symbol", ""),
        status=outcome.get("status", "unknown"),
        rating=outcome.get("rating"),
        verdict=outcome.get("verdict"),
        report_md=state.get("report_md", ""),
    )


# -- spec ---------------------------------------------------------------------------------------


def _node(id_: str, label: str, kind: NodeKind, phase: str, fn: Any, writes: set[str], **kw: Any) -> NodeSpec:
    return NodeSpec(id=id_, label=label, kind=kind, phase=phase, fn=fn, writes=frozenset(writes), **kw)


def _router(source: str, fn: Any, path_map: dict[str, str]) -> RouterSpec:
    returns = frozenset(k for k in path_map if k != "__fallback__")
    return RouterSpec(source=source, fn=fn, path_map=path_map, returns=returns)


_DEBATE_MAP = {
    "bull": "bull_researcher",
    "bear": "bear_researcher",
    "manager": "research_manager",
    "__fallback__": "research_manager",
}
_RISK_MAP = {
    "aggressive": "aggressive",
    "conservative": "conservative",
    "neutral": "neutral",
    "pm": "portfolio_manager",
    "__fallback__": "portfolio_manager",
}

TRADING_WORKFLOW = WorkflowSpec(
    id="trading",
    version="3",  # 2: refresh_price node; 3: horizon bars + event_risk (old checkpoints must not resume)
    input_model=TradeRunInput,
    output_model=TradeRunOutput,
    state_model=TradingState,
    reducers={"reports": "merge"},
    nodes=[
        _node(
            "resolve_symbol", "Resolve symbol", NodeKind.COMPUTE, "retrieve", resolve_symbol, {"instrument"}
        ),
        _node(
            "load_context",
            "Load context",
            NodeKind.COMPUTE,
            "retrieve",
            load_context,
            {"market_snapshot", "event_risk", "portfolio_ctx", "past_lessons", "invest_debate"},
        ),
        _node("analysts", "Analysts", NodeKind.AGENT, "analyze", analysts, {"reports"}),
        _node("bull_researcher", "Bull", NodeKind.AGENT, "analyze", bull_researcher, {"invest_debate"}),
        _node("bear_researcher", "Bear", NodeKind.AGENT, "analyze", bear_researcher, {"invest_debate"}),
        _node(
            "research_manager",
            "Research manager",
            NodeKind.AGENT,
            "synthesize",
            research_manager,
            {"research_plan"},
        ),
        _node("trader", "Trader", NodeKind.AGENT, "compute", trader, {"trade_proposal", "risk_debate"}),
        _node("aggressive", "Aggressive", NodeKind.AGENT, "verify", aggressive, {"risk_debate"}),
        _node("conservative", "Conservative", NodeKind.AGENT, "verify", conservative, {"risk_debate"}),
        _node("neutral", "Neutral", NodeKind.AGENT, "verify", neutral, {"risk_debate"}),
        _node(
            "portfolio_manager",
            "Portfolio manager",
            NodeKind.AGENT,
            "synthesize",
            portfolio_manager,
            {"final_decision"},
        ),
        _node("policy_gate", "Policy gate", NodeKind.GATE, "verify", policy_gate, {"policy"}),
        _node("refresh_price", "Live price", NodeKind.COMPUTE, "compute", refresh_price, {"live"}),
        _node("approval", "Approval", NodeKind.APPROVAL, "verify", approval, {"approval"}),
        _node(
            "record_decision",
            "Record decision",
            NodeKind.COMPUTE,
            "report",
            record_decision,
            {"decision_record"},
            idempotent=False,
        ),
        _node("report", "Report", NodeKind.REPORT, "report", report, {"report_md", "outcome"}),
    ],
    edges=[
        ("analysts", "bull_researcher"),
        ("trader", "aggressive"),
        ("record_decision", "report"),
        ("report", "END"),
    ],
    routers=[
        _router(
            "resolve_symbol",
            route_after_resolve,
            {"ok": "load_context", "invalid": "report", "__fallback__": "report"},
        ),
        _router(
            "load_context",
            route_after_context,
            {"ok": "analysts", "unavailable": "report", "__fallback__": "report"},
        ),
        _router("bull_researcher", should_continue_debate, _DEBATE_MAP),
        _router("bear_researcher", should_continue_debate, _DEBATE_MAP),
        _router(
            "research_manager",
            route_after_research,
            {"trade": "trader", "reject": "report", "__fallback__": "trader"},
        ),
        _router("aggressive", should_continue_risk, _RISK_MAP),
        _router("conservative", should_continue_risk, _RISK_MAP),
        _router("neutral", should_continue_risk, _RISK_MAP),
        _router(
            "portfolio_manager",
            route_after_pm,
            {"gate": "policy_gate", "reject": "report", "__fallback__": "policy_gate"},
        ),
        _router(
            "policy_gate",
            route_after_policy,
            {"approve": "refresh_price", "retry": "trader", "reject": "report", "__fallback__": "report"},
        ),
        _router(
            "refresh_price",
            route_after_refresh,
            {"approve": "approval", "reject": "report", "__fallback__": "approval"},
        ),
        _router(
            "approval",
            route_after_approval,
            {"record": "record_decision", "report": "report", "__fallback__": "report"},
        ),
    ],
    entry="resolve_symbol",
    signature_fields=[
        "analysts",
        "debate_rounds",
        "risk_rounds",
        "use_portfolio",
        "portfolio_id",
        "horizon",
        "backtest_id",
    ],
)
