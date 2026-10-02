"""Fake tools for executor / run tests (no network)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import BaseModel

from app.domain.models.provenance import new_record
from app.domain.ports import ToolCallReq
from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec

timeline: list[tuple[str, str, float]] = []  # (tool/arg, "start"/"end", t)


class SymArgs(BaseModel):
    symbol: str


class DelayArgs(BaseModel):
    label: str
    delay: float = 0.05


async def _slow(args: DelayArgs, ctx: ToolContext) -> ToolOutput:
    timeline.append((args.label, "start", time.monotonic()))
    await asyncio.sleep(args.delay)
    timeline.append((args.label, "end", time.monotonic()))
    return ToolOutput(data={"label": args.label}, summary=args.label)


async def _boom(args: DelayArgs, ctx: ToolContext) -> ToolOutput:
    raise RuntimeError(f"boom {args.label}")


async def _big(args: DelayArgs, ctx: ToolContext) -> ToolOutput:
    return ToolOutput(data="x" * 50_000, summary="big")


async def _progress(args: DelayArgs, ctx: ToolContext) -> ToolOutput:
    await ctx.progress("halfway")
    return ToolOutput(data="done", summary="done")


class SkillArgs(BaseModel):
    symbol: str


async def _skill(args: SkillArgs, ctx: ToolContext) -> ToolOutput:
    results = await ctx.call_tools(
        [ToolCallReq(id="sub_1", name="fake_prices", args={"symbol": args.symbol})]
    )
    return ToolOutput(data={"sub": [r.summary for r in results]}, summary="skill done")


async def _prices(args: SymArgs, ctx: ToolContext) -> ToolOutput:
    rec = new_record("data", 34.5, source="fake_vendor", params={"symbol": args.symbol}, unit="THB")
    return ToolOutput(
        data={"symbol": args.symbol, "close": 34.5}, summary=f"{args.symbol} · 1 bar", records=[rec]
    )


async def _indicators(args: SymArgs, ctx: ToolContext) -> ToolOutput:
    price = new_record("data", 34.5, source="fake_vendor", params={"symbol": args.symbol}, unit="THB")
    rsi = new_record(
        "compute", {"rsi14": 55.2}, source="compute", calculation="compute.fake.rsi@1", inputs=[price.id]
    )
    return ToolOutput(data={"rsi14": 55.2}, summary=f"{args.symbol} RSI14 55.2", records=[price, rsi])


def spec(name: str, fn: Any, args_model: type[BaseModel] = DelayArgs, **kw: Any) -> ToolSpec:
    return ToolSpec(name=name, description=name, args_model=args_model, category="data", fn=fn, **kw)


def all_tools() -> list[ToolSpec]:
    return [
        spec("slow_read", _slow),
        spec("slow_write", _slow, read_only=False),
        spec("boom", _boom),
        spec("big", _big),
        spec("progress", _progress),
        spec("sleepy", _slow, timeout_s=0.05),
        spec("needs_ok", _slow, requires_approval=True, read_only=False),
        spec("fake_prices", _prices, SymArgs),
        spec("fake_snapshot", _prices, SymArgs, limit_by="symbol"),
        ToolSpec(name="skill_tool", description="skill", args_model=SkillArgs, category="skill", fn=_skill),
        spec("get_prices", _prices, SymArgs),
        spec("compute_indicators", _indicators, SymArgs),
    ]
