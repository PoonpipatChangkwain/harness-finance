from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.domain.ports import ToolCallReq
from app.harness.executor.executor import ToolExecutor
from app.harness.executor.permissions import PermissionPolicy
from app.harness.executor.registry import ToolRegistry
from app.harness.runtime.limits import RunLimits
from tests.harness import fake_tools
from tests.harness.conftest import RecordingEmitter, make_ctx

pytestmark = pytest.mark.usefixtures("db")


def _executor(tmp_path: Path, **kw: object) -> tuple[ToolExecutor, RecordingEmitter]:
    ctx = make_ctx(tmp_path, limits=kw.pop("limits", None))  # type: ignore[arg-type]
    registry = ToolRegistry(fake_tools.all_tools(), capability_available=lambda cap: True)
    executor = ToolExecutor(registry, ctx, **kw)  # type: ignore[arg-type]
    assert isinstance(ctx.emit, RecordingEmitter)
    return executor, ctx.emit


def _call(name: str, call_id: str, **args: object) -> ToolCallReq:
    return ToolCallReq(id=call_id, name=name, args=args)


def _window(label: str) -> tuple[float, float]:
    start = next(t for lbl, kind, t in fake_tools.timeline if lbl == label and kind == "start")
    end = next(t for lbl, kind, t in fake_tools.timeline if lbl == label and kind == "end")
    return start, end


@pytest.fixture(autouse=True)
def _clear_timeline() -> None:
    fake_tools.timeline.clear()


async def test_read_only_calls_run_concurrently(tmp_path: Path) -> None:
    executor, emit = _executor(tmp_path)
    calls = [_call("slow_read", f"c{i}", label=f"r{i}", delay=0.1) for i in range(3)]
    results = await executor.run_calls(calls)
    assert [r.status for r in results] == ["ok"] * 3
    windows = [_window(f"r{i}") for i in range(3)]
    # every call started before any of them ended → they overlapped
    assert max(s for s, _ in windows) < min(e for _, e in windows)
    batch_ids = {e["payload"]["batch_id"] for e in emit.events if e["type"] == "tool.started"}
    assert len(batch_ids) == 1


async def test_side_effect_calls_run_in_order(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path)
    calls = [_call("slow_write", f"w{i}", label=f"w{i}", delay=0.03) for i in range(3)]
    await executor.run_calls(calls)
    windows = [_window(f"w{i}") for i in range(3)]
    assert windows[0][1] <= windows[1][0] and windows[1][1] <= windows[2][0]


async def test_validation_error_is_a_result(tmp_path: Path) -> None:
    executor, emit = _executor(tmp_path)
    [result] = await executor.run_calls([_call("slow_read", "c1", delay="soon")])
    assert result.status == "error" and not result.ok
    assert result.error is not None and result.error.type == "ValidationError"
    assert "label" in result.error.message and "delay" in result.error.message
    assert emit.types() == ["tool.started", "tool.finished"]


async def test_timeout(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path)
    [result] = await executor.run_calls([_call("sleepy", "c1", label="s", delay=1.0)])
    assert result.status == "timeout"
    assert result.error is not None and result.error.retryable


async def test_exception_becomes_error(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path)
    [result] = await executor.run_calls([_call("boom", "c1", label="x")])
    assert result.status == "error"
    assert (
        result.error is not None and result.error.type == "RuntimeError" and "boom x" in result.error.message
    )


async def test_cancel_stops_every_call(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path)
    calls = [_call("slow_read", f"c{i}", label=f"k{i}", delay=5) for i in range(3)]

    async def cancel_soon() -> None:
        await asyncio.sleep(0.05)
        executor.ctx.cancel.cancel()

    results, _ = await asyncio.gather(executor.run_calls(calls), cancel_soon())
    assert [r.status for r in results] == ["cancelled"] * 3


async def test_big_output_truncated_but_scratchpad_full(tmp_path: Path) -> None:
    executor, emit = _executor(tmp_path)
    [result] = await executor.run_calls([_call("big", "c1", label="b")])
    assert result.truncated
    assert len(result.content) < 13_000 and "call_id=c1" in result.content
    finished = next(e for e in emit.events if e["type"] == "tool.finished")
    assert len(finished["payload"]["preview"]) == 2_000
    [entry] = executor.ctx.scratchpad.tool_results()
    assert len(entry["result"]) == 50_000


async def test_per_tool_limit_and_similar_query(tmp_path: Path) -> None:
    executor, emit = _executor(tmp_path)
    first = await executor.run_calls([_call("fake_prices", "c1", symbol="PTT")])
    repeat = await executor.run_calls([_call("fake_prices", "c2", symbol="PTT")])
    assert first[0].status == "ok"
    assert repeat[0].status == "limited"
    limits = [e["payload"] for e in emit.events if e["type"] == "guard.limit"]
    assert limits[-1]["kind"] == "similar_query"

    for i, sym in enumerate(["AOT", "CPALL"]):
        [r] = await executor.run_calls([_call("fake_prices", f"d{i}", symbol=sym)])
        assert r.status == "ok"
    [fourth] = await executor.run_calls([_call("fake_prices", "d9", symbol="KBANK")])
    assert fourth.status == "limited"
    assert emit.events[-2]["payload"]["kind"] == "tool_calls"


async def test_per_symbol_limit_allows_whole_portfolio(tmp_path: Path) -> None:
    """``limit_by="symbol"``: 11 holdings in one turn all run; the budget is per symbol."""
    executor, _ = _executor(tmp_path)
    symbols = ["NVDA", "AMZN", "MSFT", "AAPL", "GOOGL", "OXY", "DPZ", "V", "O", "QQQI", "MRK"]
    calls = [_call("fake_snapshot", f"s{i}", symbol=s) for i, s in enumerate(symbols)]
    results = await executor.run_calls(calls)
    assert [r.status for r in results] == ["ok"] * len(symbols)
    # the same symbol again (any case) is still caught as a repeat
    [again] = await executor.run_calls([_call("fake_snapshot", "s99", symbol="nvda ")])
    assert again.status == "limited"


async def test_run_wide_tool_budget(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path, limits=RunLimits(max_tool_calls_total=2))
    results = await executor.run_calls(
        [_call("slow_read", f"c{i}", label=f"t{i}", delay=0) for i in range(3)]
    )
    assert [r.status for r in results].count("limited") == 1


async def test_approval_and_deny(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path, policy=PermissionPolicy({"boom": "deny"}))
    [ask] = await executor.run_calls([_call("needs_ok", "c1", label="x")])
    assert ask.status == "denied" and ask.error is not None and ask.error.type == "ApprovalUnavailable"
    [deny] = await executor.run_calls([_call("boom", "c2", label="x")])
    assert deny.status == "denied"
    executor.policy.approve_for_session("needs_ok")
    [ok] = await executor.run_calls([_call("needs_ok", "c3", label="y", delay=0)])
    assert ok.status == "ok"


async def test_unknown_tool(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path)
    [r] = await executor.run_calls([_call("nope", "c1")])
    assert r.status == "error" and r.error is not None and r.error.type == "UnknownTool"


async def test_malformed_tool_name_is_repaired(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path)
    [prefixed] = await executor.run_calls([_call("functions.fake_prices", "c1", symbol="PTT")])
    assert prefixed.status == "ok" and prefixed.tool == "fake_prices"
    [generic] = await executor.run_calls([_call("function", "c2", symbol="PTT")])
    assert generic.tool == "fake_prices" or generic.error is not None  # only repaired if args match uniquely


async def test_progress_and_subcalls(tmp_path: Path) -> None:
    executor, emit = _executor(tmp_path)
    await executor.run_calls([_call("progress", "p1", label="p")])
    assert any(e["type"] == "tool.progress" and e["call_id"] == "p1" for e in emit.events)
    [skill] = await executor.run_calls([_call("skill_tool", "s1", symbol="PTT")])
    assert skill.status == "ok"
    sub_started = next(e for e in emit.events if e["type"] == "tool.started" and e["call_id"] == "sub_1")
    assert sub_started["parent_call_id"] == "s1"


async def test_provenance_recorded_with_run_and_tool(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path)
    [r] = await executor.run_calls([_call("fake_prices", "c1", symbol="PTT")])
    assert len(r.provenance_ids) == 1
    record = await executor.provenance.get(r.provenance_ids[0])
    assert record is not None and record.run_id == "run_TEST" and record.tool == "fake_prices"
