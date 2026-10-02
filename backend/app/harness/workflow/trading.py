"""Harness entry point of the ``trading`` workflow (P10): wire services, run the spec.

- ``TradingAgent`` implements ``AgentPort`` for the analysts: every ``run_loop`` is a
  fresh ``AgentLoop`` (own conversation, own context manager, the role's tool subset),
  so the concurrent analysts never share a message history. Its events carry the
  sub-node ``analyst.<name>`` (bracketed by their own ``node.started``/``node.finished``)
  so the UI can draw one live node per analyst (P11).
- ``MemoryDecisionLog`` implements the domain's ``DecisionLogPort`` on the memory
  store (namespace ``trade_decisions``; keyed by run, so a re-run of
  ``record_decision`` after a crash overwrites rather than duplicates).
- checkpoint scope = the symbol's canonical form, thread date = the analysis date
  (latest bar when unset): TradingAgents' one-analysis-per-symbol-per-day threads (T3).
"""

from __future__ import annotations

import copy
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

from app.domain.agents.library import get_role, role_profile
from app.domain.data.backtest import PointInTimeRouter, PriceStore, preload_prices
from app.domain.data.registry import get_router, use_router
from app.domain.data.router import DataRouter
from app.domain.models.horizon import horizon
from app.domain.models.symbol import normalize_symbol, safe_path_component
from app.domain.ports import AgentRunRequest, AgentRunResult, AnswerCheck, LLMUsage
from app.domain.workflows.trade_outcome import TradeOutcomeInput, reflection_messages, resolve_outcome
from app.domain.workflows.trading import ANALYST_ROLES, TRADING_WORKFLOW, output_of
from app.harness.config.overrides import trading_policy_config
from app.harness.context.config import ContextConfig
from app.harness.context.manager import ContextManager
from app.harness.executor.executor import ToolExecutor
from app.harness.graph.context import make_node_context_factory
from app.harness.runtime.agent_loop import AgentLoop
from app.harness.runtime.context import RunContext
from app.harness.runtime.prompts import current_date, tool_section
from app.harness.state.memory import MemoryStore
from app.harness.workflow.runner import GraphRunner
from app.harness.workflow.sequential import ScopedEmitter

DECISIONS_NS = "trade_decisions"
# role → (sub-node id, label) for the UI; the portfolio advisor passes its own map
ANALYST_SUB_NODES = {role: (f"analyst.{name}", f"{name} analyst") for name, role in ANALYST_ROLES.items()}


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def trade_scope(input: dict[str, Any]) -> str:
    try:
        return safe_path_component(normalize_symbol(str(input.get("symbol", ""))).canonical)
    except ValueError:
        return "_invalid_symbol"


def trade_date(run_id: str, input: dict[str, Any]) -> str:
    return str(input.get("analysis_date") or "latest")


class MemoryDecisionLog:
    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    async def append(self, record: dict[str, Any]) -> str:
        key = f"{record['symbol']}:{record.get('date')}:{record['run_id']}"
        await self.memory.put(DECISIONS_NS, key, {**record, "recorded_at": datetime.now(UTC).isoformat()})
        return key

    async def lessons(
        self, symbol: str, limit: int = 3, others: int = 0, *, before: date | None = None
    ) -> list[str]:
        items = await self.memory.search(DECISIONS_NS, prefix="" if others else f"{symbol}:", limit=10_000)
        # a settled decision teaches through its reflection and/or the trader's own note
        settled = [i.value for i in items if isinstance(i.value, dict) and _has_lesson(i.value)]
        if before is not None:  # point-in-time: only what was known on the analysis date
            settled = [v for v in settled if _settled_before(v, before)]
        # the most recently settled lessons (the store orders by key, i.e. by decision date)
        settled.sort(key=lambda v: str((v.get("outcome") or {}).get("settled_at") or ""))
        same = [v for v in settled if v.get("symbol") == symbol]
        other = [v for v in settled if v.get("symbol") != symbol]
        picked = same[-limit:] if limit > 0 else []
        picked += other[-others:] if others > 0 else []
        return [_lesson(v) for v in picked]

    async def find(self, run_id: str) -> tuple[str, dict[str, Any]] | None:
        for item in await self.memory.search(DECISIONS_NS, limit=10_000):
            if item.key.endswith(f":{run_id}") and isinstance(item.value, dict):
                return item.key, item.value
        return None

    async def list(self, symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        items = await self.memory.search(DECISIONS_NS, prefix=f"{symbol}:" if symbol else "", limit=10_000)
        rows = [{"key": i.key, **i.value} for i in items if isinstance(i.value, dict)]
        rows.sort(key=lambda r: str(r.get("recorded_at") or ""), reverse=True)
        return rows[:limit]

    async def save(self, key: str, value: dict[str, Any]) -> None:
        await self.memory.put(DECISIONS_NS, key, value)


class NullDecisionLog:
    """(P15) A backtest cell: no lessons in (they would come from the live, later log), none out.

    The decision still lands in the run's state (``decision_record``), which the backtest reads.
    """

    async def append(self, record: dict[str, Any]) -> str:
        return f"{record['symbol']}:{record.get('date')}:{record['run_id']}"

    async def lessons(
        self, symbol: str, limit: int = 3, others: int = 0, *, before: date | None = None
    ) -> list[str]:
        return []


class DecisionNotFound(LookupError):
    pass


async def settle_trade(services: Any, run_id: str, inp: TradeOutcomeInput) -> dict[str, Any]:
    """Record how a decided trade ended (the human's tick), compute R, and write the lesson.

    Re-settling overwrites (a mis-tick can be corrected). A failed reflection keeps the
    outcome and records the error; the lesson can be regenerated by settling again.
    Raises ``DecisionNotFound`` or ``OutcomeError``.
    """
    log = MemoryDecisionLog(services.memory)
    found = await log.find(run_id)
    if found is None:
        raise DecisionNotFound(f"no recorded decision for run {run_id}")
    key, record = found
    outcome, records = resolve_outcome(record.get("card") or {}, inp, inputs=record.get("provenance_ids"))
    if records:
        outcome["provenance_ids"] = [str(i) for i in await services.provenance.add_many(records)]
    reflection: str | None = None
    error: str | None = None
    if inp.outcome != "cancelled":
        language = "Thai" if record.get("language", "th") == "th" else "English"
        try:
            resp = await services.get_llm().complete(
                reflection_messages(record, outcome, language), tier="quick", purpose="reflection"
            )
            reflection = resp.text.strip() or None
        except Exception as exc:  # the outcome is still worth keeping without its lesson
            error = f"{type(exc).__name__}: {exc}"
    value = {**record, "outcome": outcome, "reflection": reflection, "reflection_error": error}
    await log.save(key, value)
    return {"key": key, **value}


def _settled_before(value: dict[str, Any], day: date) -> bool:
    """Settled strictly before ``day``; an undated settlement cannot be proven past, so it is dropped."""
    settled_at = str((value.get("outcome") or {}).get("settled_at") or "")
    try:
        return datetime.fromisoformat(settled_at.replace("Z", "+00:00")).date() < day
    except ValueError:
        return False


def _has_lesson(value: dict[str, Any]) -> bool:
    return bool(value.get("reflection") or ((value.get("outcome") or {}).get("note") or "").strip())


def _lesson(value: dict[str, Any]) -> str:
    """A stored reflection plus the trader's note verbatim, prefixed with what it is about so a
    later run can weigh it (the note is not left to the reflector's paraphrase)."""
    card = value.get("card") or {}
    outcome = value.get("outcome") or {}
    r = outcome.get("r_multiple")
    head = (
        f"[{value.get('symbol') or card.get('symbol')} {value.get('date')} {card.get('horizon', '')} "
        f"{card.get('side', '')} → {outcome.get('label', '?')}"
        + (f", {r:+.2f}R" if isinstance(r, int | float) else "")
        + "]"
    )
    parts = [head]
    if value.get("reflection"):
        parts.append(str(value["reflection"]))
    note = (outcome.get("note") or "").strip()
    if note:
        parts.append(f'Trader\'s note: "{note}"')
    return " ".join(parts)


class TradingAgent:
    def __init__(
        self,
        ctx: RunContext,
        llm: Any,
        executor: ToolExecutor,
        *,
        deep_model: str,
        quick_model: str,
        sub_nodes: dict[str, tuple[str, str]] | None = None,
        phase: str = "analyze",
    ):
        self.ctx = ctx
        self.llm = llm
        self.executor = executor
        self.deep_model = deep_model
        self.quick_model = quick_model
        self.sub_nodes = ANALYST_SUB_NODES if sub_nodes is None else sub_nodes
        self.phase = phase

    async def run_loop(self, req: AgentRunRequest) -> AgentRunResult:
        services = self.ctx.services
        role = get_role(req.role)
        tools = services.tools.for_node(role.tools) if role.tools else []
        system = (
            f"{role_profile(role)}\n\nCurrent date: {current_date()}\n\n"
            f"## Available Tools\n\n{tool_section(tools)}"
        )
        # each concurrent analyst tags its events with its own sub-node (``analyst.<name>``)
        # so the UI can show one live node per analyst inside the single ``analysts`` node
        sub, label = self.sub_nodes.get(req.role, (None, None))
        ctx, llm, executor = self.ctx, self.llm, self.executor
        if sub is not None:
            base = self.ctx.emit.base if isinstance(self.ctx.emit, ScopedEmitter) else self.ctx.emit
            emit = ScopedEmitter(base)
            emit.node = sub
            ctx = copy.copy(self.ctx)  # shallow: usage, limits, scratchpad and cancel stay shared
            ctx.emit = emit
            ctx.guard_scope = sub
            llm = self.llm.bind(emit=emit, node=sub)
            executor = ToolExecutor(services.tools, ctx, provenance=services.provenance)
        context = ContextManager(
            llm,
            services.tools,
            model=self.deep_model,
            summary_model=self.quick_model,
            config=ContextConfig.from_settings(services.settings),
            memory=None,
        )
        loop = AgentLoop(ctx, llm, executor, context)
        loop.start(system, [], req.instruction or "")
        if sub is not None:
            await ctx.emit.emit("node.started", {"node": sub, "label": label, "phase": self.phase})
        start = time.monotonic()
        try:
            outcome = await loop.run(
                tools,
                max_iterations=req.max_iterations,
                require_data=req.require_data,
                wrap_up=req.wrap_up,
            )
        except BaseException:
            if sub is not None:
                await ctx.emit.emit(
                    "node.finished", {"node": sub, "status": "error", "duration_ms": _ms(start)}
                )
            raise
        if sub is not None:
            status = "done" if outcome.draft else "error"
            summary = (
                f"{outcome.iterations} iterations · {outcome.tool_calls} tools"
                f" · {outcome.stop_reason or 'ok'}"
            )
            await ctx.emit.emit(
                "node.finished",
                {"node": sub, "status": status, "duration_ms": _ms(start), "summary": summary},
            )
        return AgentRunResult(
            iterations=outcome.iterations,
            stop_reason=outcome.stop_reason,
            draft=outcome.draft,
            tool_calls=outcome.tool_calls,
        )

    async def check(self, markdown: str) -> AnswerCheck:
        return AnswerCheck(markdown=markdown)

    async def final_answer(self, *, role: str, stop_reason: str | None) -> AnswerCheck:
        raise NotImplementedError("the trading workflow writes its own report")


def _runner(ctx: RunContext) -> GraphRunner:
    settings = ctx.services.settings
    return GraphRunner(
        TRADING_WORKFLOW,
        checkpoints=ctx.services.checkpoints,
        scope=trade_scope,
        date_fn=trade_date,
        keep_checkpoints_on_success=False,  # T3: clear on success
        recursion_limit=settings.graph_recursion_limit,
    )


def _build(ctx: RunContext) -> Any:
    services = ctx.services
    scoped = ScopedEmitter(ctx.emit)
    ctx.emit = scoped

    def on_usage(usage: LLMUsage, purpose: str) -> None:
        ctx.record_llm_usage(usage, purpose)

    choice = ctx.input.get("llm") if isinstance(ctx.input.get("llm"), dict) else None
    base_llm = (
        services.get_llm_for_choice(choice["provider"], choice["model"]) if choice else services.get_llm()
    )
    llm = base_llm.bind(emit=scoped, on_usage=on_usage)
    executor = ToolExecutor(services.tools, ctx, provenance=services.provenance)
    agent = TradingAgent(
        ctx, llm, executor, deep_model=base_llm.primary("deep")[1], quick_model=base_llm.primary("quick")[1]
    )
    backtest = bool(ctx.input.get("backtest_id"))
    return make_node_context_factory(
        llm=llm,
        tools=executor,
        agent=agent,
        run_id=ctx.run_id,
        config={
            # (P15) a backtest cell neither reads nor writes the live decision log, and has no human
            "decision_log": NullDecisionLog() if backtest else MemoryDecisionLog(services.memory),
            "auto_approve": backtest,
            "default_notional": 1_000_000.0,
            "portfolio": services.portfolio.trading_context,  # (P12) the saved book, 3-state
            **trading_policy_config(services.settings),  # (P13) Settings-page policy limits
        },
    )


STATE_KEYS = (
    "instrument", "market_snapshot", "portfolio_ctx", "reports", "invest_debate", "research_plan",
    "trade_proposal", "risk_debate", "final_decision", "policy", "approval", "decision_record", "outcome",
)  # fmt: skip


def _finish(ctx: RunContext, state: dict[str, Any]) -> dict[str, Any]:
    """The run's output keeps the final state: on success the checkpoint is cleared (T3),
    and ``GET /trade/runs/{id}`` still needs the per-node results."""
    out = output_of(state)
    return {**out.model_dump(), "state": {k: state.get(k) for k in STATE_KEYS}}


@asynccontextmanager
async def point_in_time(input: dict[str, Any], base: DataRouter) -> AsyncIterator[None]:
    """(P15) A run dated in the past sees the data as of that day, whatever tool asks.

    A backtest already installed its own router for the cell (inherited by this task); a single
    past-dated run from ``/trade`` preloads its symbol here. Today / no date: the live router.
    """
    day = input.get("analysis_date")
    if not day or isinstance(get_router(), PointInTimeRouter) or date.fromisoformat(day) >= date.today():
        yield
        return
    as_of = date.fromisoformat(day)
    sym = normalize_symbol(str(input["symbol"]))
    store = PriceStore()
    intervals: list[str] = sorted({horizon(input.get("horizon")).interval, "1d"})
    await preload_prices(base, store, [sym], intervals, as_of, as_of)
    with use_router(PointInTimeRouter(base, store, as_of)):
        yield


async def trading_workflow(ctx: RunContext) -> dict[str, Any] | None:
    factory = _build(ctx)
    async with point_in_time(ctx.input, ctx.services.router()):
        state = await _runner(ctx).start(ctx, factory)
    return None if state is None else _finish(ctx, state)


async def trading_workflow_resume(ctx: RunContext, resume_value: Any) -> dict[str, Any] | None:
    factory = _build(ctx)
    async with point_in_time(ctx.input, ctx.services.router()):
        state = await _runner(ctx).resume(ctx, factory, resume_value=resume_value)
    return None if state is None else _finish(ctx, state)
