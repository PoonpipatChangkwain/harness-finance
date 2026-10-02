from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from app.domain.ports import LLMUsage
from app.harness.runtime.context import RunContext
from app.harness.runtime.limits import LimitExceeded, LimitGuard, LimitHit, RunLimits
from app.harness.runtime.retry import with_retry
from app.harness.storage import events_repo, runs_repo
from app.settings import Settings
from tests.harness import fake_tools
from tests.harness.conftest import RecordingEmitter, make_ctx, make_services


def test_guard_kinds(tmp_path: Path) -> None:
    ctx = make_ctx(
        tmp_path,
        limits=RunLimits(
            max_iterations=2,
            max_tool_calls_total=5,
            max_cost_usd=0.5,
            max_output_tokens_total=1000,
            run_wall_clock_s=60,
        ),
    )
    guard = LimitGuard(ctx.limits)
    assert guard.check(ctx) is None
    ctx.iteration = 2
    assert (hit := guard.check(ctx)) and hit.kind == "iterations"
    ctx.iteration = 0
    ctx.usage.tool_calls = 5
    assert (hit := guard.check(ctx, pending_tool_calls=1)) and hit.kind == "tool_calls"
    ctx.usage.tool_calls = 0
    ctx.record_llm_usage(LLMUsage(input_tokens=10, output_tokens=10, cost_usd=0.6))
    assert (hit := guard.check(ctx)) and hit.kind == "budget"
    ctx.usage.cost_usd = 0
    ctx.usage.output_tokens = 1000
    assert (hit := guard.check(ctx)) and hit.kind == "budget"
    ctx.usage.output_tokens = 0
    ctx.started_monotonic = time.monotonic() - 61
    assert (hit := guard.check(ctx)) and hit.kind == "timeout"
    assert ctx.last_api_input_tokens == 10


async def test_enforce_emits_guard_limit(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, limits=RunLimits(max_iterations=1))
    ctx.iteration = 1
    await ctx.guard.enforce(ctx)
    assert isinstance(ctx.emit, RecordingEmitter)
    assert ctx.emit.events == [
        {
            "type": "guard.limit",
            "payload": {"kind": "iterations", "detail": "reached 1 iterations"},
            "node": None,
            "call_id": None,
            "parent_call_id": None,
        }
    ]


def test_limits_from_settings() -> None:
    s = Settings(_env_file=None, run_max_iterations=4, run_max_cost_usd=0.25)  # type: ignore[call-arg]
    limits = RunLimits.from_settings(s, max_tool_calls_total=7)
    assert (limits.max_iterations, limits.max_cost_usd, limits.max_tool_calls_total) == (4, 0.25, 7)


async def test_generic_retry_only_retries_transient() -> None:
    calls = 0

    async def flaky() -> int:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise ConnectionError("reset")
        return calls

    async def no_sleep(_: float) -> None:
        return None

    assert await with_retry(flaky, retryable=lambda e: isinstance(e, ConnectionError), sleep=no_sleep) == 2


# -- RunManager -------------------------------------------------------------------------------


async def _finished(run_id: str) -> dict[str, Any]:
    rows = await events_repo.events_after(run_id, 0)
    return rows[-1].payload | {"type": rows[-1].type, "types": [r.type for r in rows]}


@pytest.mark.usefixtures("db")
async def test_run_completes(settings: Settings) -> None:
    services = make_services(settings, fake_tools.all_tools())

    async def ok(ctx: RunContext) -> dict[str, Any]:
        return {"answer": 42}

    services.runs.register("ok", ok)
    run_id = await services.runs.start("ok", {"query": "q"})
    await services.runs.wait(run_id)
    end = await _finished(run_id)
    assert end["type"] == "run.finished" and end["status"] == "completed"
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "completed" and run.output == {"answer": 42}


@pytest.mark.usefixtures("db")
async def test_uncaught_exception_is_reported(settings: Settings) -> None:
    services = make_services(settings, [])

    async def bad(ctx: RunContext) -> None:
        raise ValueError("kaboom")

    services.runs.register("bad", bad)
    run_id = await services.runs.start("bad", {})
    await services.runs.wait(run_id)
    end = await _finished(run_id)
    assert end["status"] == "failed" and "kaboom" in end["error"]


@pytest.mark.usefixtures("db")
async def test_cancel(settings: Settings) -> None:
    services = make_services(settings, [])

    async def forever(ctx: RunContext) -> None:
        await asyncio.sleep(60)

    services.runs.register("forever", forever)
    run_id = await services.runs.start("forever", {})
    await asyncio.sleep(0.01)
    assert await services.runs.cancel(run_id)
    end = await _finished(run_id)
    assert end["status"] == "cancelled"
    assert not services.runs.is_running(run_id)


@pytest.mark.usefixtures("db")
async def test_wall_clock_and_guard_limit(settings: Settings) -> None:
    services = make_services(settings, [])

    async def slow(ctx: RunContext) -> None:
        await asyncio.sleep(5)

    async def limited(ctx: RunContext) -> None:
        raise LimitExceeded(LimitHit(kind="budget", detail="cost cap"))

    services.runs.register("slow", slow)
    services.runs.register("limited", limited)
    run_id = await services.runs.start("slow", {}, limits=RunLimits(run_wall_clock_s=0.05))
    await services.runs.wait(run_id)
    end = await _finished(run_id)
    assert end["status"] == "limited" and "guard.limit" in end["types"]

    run_id = await services.runs.start("limited", {})
    await services.runs.wait(run_id)
    assert (await _finished(run_id))["reason"] == "cost cap"


@pytest.mark.usefixtures("db")
async def test_recover_interrupted(settings: Settings) -> None:
    await runs_repo.create_run("run_OLD", "x", {})
    services = make_services(settings, [])
    assert await services.runs.recover_interrupted() == ["run_OLD"]
    run = await runs_repo.get_run("run_OLD")
    assert run is not None and run.status == "interrupted"
