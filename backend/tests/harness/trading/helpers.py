"""Builders for trading-workflow tests: fake snapshot tool, real ``size_trade``, scripted LLM."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.domain.agents.schemas import PortfolioDecision, TradeResearchPlan, TraderProposal
from app.domain.data.router import DataRouter
from app.domain.models.decision import Decision, Rating, Verdict
from app.domain.models.provenance import new_record
from app.domain.ports import LLMMessage, LLMResponse
from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec
from app.domain.tools.common import to_symbol
from app.domain.tools.trading import TRADING_TOOLS, TradeEventRiskArgs, TradeQuoteArgs, TradeSnapshotArgs
from app.harness.events.bus import EventBus
from app.harness.executor.registry import ToolRegistry
from app.harness.llm.fake import FakeLLM, ScriptItem
from app.harness.runtime.services import HarnessServices
from app.harness.state.memory import MemoryStore
from app.harness.storage import events_repo
from app.settings import Settings

SNAPSHOTS: dict[str, dict[str, Any]] = {
    "PTT.BK": {"close": 34.5, "atr14": 0.6},
    "AAPL": {"close": 200.0, "atr14": 4.0},
    "EURUSD=X": {"close": 1.08, "atr14": 0.006},
    "BTC-USD": {"close": 60_000.0, "atr14": 1_500.0},
    "XAUUSD": {"close": 2_400.0, "atr14": 30.0},
}


SNAPSHOT_CALLS: list[dict[str, Any]] = []  # args of every trade_snapshot call, newest last
# ATR is smaller on shorter bars (15m = a tenth); 1h keeps the SNAPSHOTS value so the numbers
# of tests on the default (swing, 1h) horizon stay the round ones they assert
_ATR_SCALE = {"1d": 1.0, "1h": 1.0, "15m": 0.1}


async def _fake_snapshot(args: TradeSnapshotArgs, ctx: ToolContext) -> ToolOutput:
    SNAPSHOT_CALLS.append(args.model_dump(mode="json"))
    sym = to_symbol(args.symbol, args.market)
    snap = SNAPSHOTS[sym.canonical]
    value = {
        "symbol": sym.canonical,
        "interval": args.interval,
        "as_of": "2026-09-25",
        "currency": sym.currency,
        "latest_bar": {"close": snap["close"], "ts": "2026-09-25T09:00:00+00:00"},
        "indicators": {
            "atr14": round(snap["atr14"] * _ATR_SCALE[args.interval], 8),
            "rsi14": 55.0,
            "sma50": snap["close"] * 0.98,
        },
    }
    rec = new_record(
        "compute", value, source="compute", calculation="compute.indicators.verified_snapshot@v1"
    )
    return ToolOutput(data=value, summary=f"{sym.canonical} close {snap['close']}", records=[rec])


# Live prices by symbol for ``trade_quote``: a float, a list consumed one call at a time (the last
# one repeats), or None for "no quote". Unset = the snapshot close, i.e. the price has not moved.
QUOTES: dict[str, float | list[float] | None] = {}


async def _fake_quote(args: TradeQuoteArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    price = QUOTES.get(sym.canonical, SNAPSHOTS[sym.canonical]["close"])
    if isinstance(price, list):
        price = price.pop(0) if len(price) > 1 else price[0]
    if price is None:
        raise ValueError(f"no quote for {sym.canonical}")
    value = {"symbol": sym.canonical, "price": price, "ts": "2026-09-28T03:15:00Z", "provider": "fake"}
    rec = new_record("data", price, source="fake", tool="trade_quote")
    return ToolOutput(data=value, summary=f"{sym.canonical} live {price}", records=[rec])


# Scheduled events for ``trade_event_risk``: rows as the calendar tool returns them, or None = unavailable.
EVENTS: dict[str, list[dict[str, Any]] | None] = {"rows": []}


def event(
    hours: float, impact: str = "high", currency: str = "USD", title: str = "CPI m/m"
) -> dict[str, Any]:
    return {
        "at": "2026-09-25T12:30:00+00:00",
        "hours_from_now": hours,
        "currency": currency,
        "impact": impact,
        "title": title,
        "forecast": "0.3%",
        "previous": "0.2%",
        "actual": None,
    }


async def _fake_event_risk(args: TradeEventRiskArgs, ctx: ToolContext) -> ToolOutput:
    rows = EVENTS["rows"]
    if rows is None:
        return ToolOutput(data=None, summary="economic calendar unavailable: forexfactory: transient",
                          status="unavailable")  # fmt: skip
    value = {"now": "2026-09-25T09:00:00+00:00", "currencies": ["THB", "USD"], "events": rows}
    rec = new_record("data", value, source="fake", tool="economic_calendar")
    return ToolOutput(data=value, summary=f"{len(rows)} events", records=[rec])


def trading_tools() -> list[ToolSpec]:
    size_trade = next(t for t in TRADING_TOOLS if t.name == "size_trade")
    snapshot = next(t for t in TRADING_TOOLS if t.name == "trade_snapshot")
    quote = next(t for t in TRADING_TOOLS if t.name == "trade_quote")
    events = next(t for t in TRADING_TOOLS if t.name == "trade_event_risk")
    return [
        snapshot.model_copy(update={"fn": _fake_snapshot, "requires": frozenset()}),
        quote.model_copy(update={"fn": _fake_quote, "requires": frozenset()}),
        events.model_copy(update={"fn": _fake_event_risk, "requires": frozenset()}),
        size_trade,
    ]


def trading_services(settings: Settings, llm: FakeLLM, memory: MemoryStore | None = None) -> HarnessServices:
    return HarnessServices(
        settings=settings,
        bus=EventBus(),
        tools=ToolRegistry(trading_tools(), capability_available=lambda cap: True),
        memory=memory or MemoryStore(),
        llm=llm,
        data_router=DataRouter({}),  # no network: a past-dated run's preload finds no provider
    )


class TimedFakeLLM(FakeLLM):
    """Analyst ('agent') calls take a moment and record when they ran, to observe concurrency."""

    def __init__(self, script: list[ScriptItem], delay: float = 0.05) -> None:
        super().__init__(script)
        self.delay = delay
        self.windows: list[tuple[float, float]] = []

    async def complete(
        self, messages: list[LLMMessage], *, tier: Any, tools: Any = None, purpose: str
    ) -> LLMResponse:
        if purpose != "agent":
            return await super().complete(messages, tier=tier, tools=tools, purpose=purpose)
        start = time.monotonic()
        await asyncio.sleep(self.delay)
        resp = await super().complete(messages, tier=tier, tools=tools, purpose=purpose)
        self.windows.append((start, time.monotonic()))
        return resp


def decision(verdict: Verdict = Verdict.APPROVE, confidence: float = 0.8, reason: str = "ok") -> Decision:
    return Decision(verdict=verdict, confidence=confidence, reason=reason)


def research_plan(verdict: Verdict = Verdict.APPROVE, stance: Rating = Rating.BUY) -> TradeResearchPlan:
    return TradeResearchPlan(
        stance=stance, thesis="uptrend with support", key_risks=["oil price"], catalysts=["earnings"],
        decision=decision(verdict),
    )  # fmt: skip


def proposal(
    side: str = "long",
    *,
    basis: str = "atr",
    stop_level: float | None = None,
    targets: list[float] | None = None,
    proposed_units: float | None = None,
    order_type: str = "market",
    order_price: float | None = None,
) -> TraderProposal:
    return TraderProposal(
        side=side,  # type: ignore[arg-type]
        entry_zone=[34.0, 34.8],
        order_type=order_type,  # type: ignore[arg-type]
        order_price=order_price,
        stop_basis=basis,  # type: ignore[arg-type]
        atr_multiple=2.0,
        stop_level=stop_level,
        target_levels=targets if targets is not None else [37.0, 39.0],
        rationale="buy the pullback",
        decision=decision(),
        proposed_units=proposed_units,
    )


def pm(
    verdict: Verdict = Verdict.APPROVE, fraction: float = 1.0, rating: Rating = Rating.BUY
) -> PortfolioDecision:
    return PortfolioDecision(rating=rating, decision=decision(verdict), final_size_pct=fraction, summary="go")


def analyst_turns(n: int) -> list[ScriptItem]:
    return ["analyst report: trend up, support 33.8"] * n


def debate_turns(rounds: int = 1) -> list[ScriptItem]:
    return ["bull: momentum", "bear: valuation"] * rounds


def risk_turns(rounds: int = 1) -> list[ScriptItem]:
    return ["aggressive: size up", "conservative: size down", "neutral: keep"] * rounds


def happy_script(n_analysts: int = 4) -> list[ScriptItem]:
    return [
        *analyst_turns(n_analysts),
        *debate_turns(),
        research_plan(),
        proposal(),
        *risk_turns(),
        pm(),
    ]


async def events_of(run_id: str) -> list[dict[str, Any]]:
    rows = await events_repo.events_after(run_id, 0)
    return [{"type": r.type, "payload": r.payload, "node": r.node, "seq": r.seq} for r in rows]


def of(events: list[dict[str, Any]], type_: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in events if e["type"] == type_]


def node_order(events: list[dict[str, Any]]) -> list[str]:
    """Graph nodes in start order (the per-analyst ``analyst.*`` sub-nodes left out)."""
    return [
        e["payload"]["node"]
        for e in events
        if e["type"] == "node.started" and not e["payload"]["node"].startswith("analyst.")
    ]
