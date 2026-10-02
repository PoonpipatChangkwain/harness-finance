from __future__ import annotations

from pathlib import Path

from app.domain.tools.base import ToolSpec
from app.harness.context.config import ContextConfig
from app.harness.context.messages import assert_tool_pairing
from app.harness.context.microcompact import is_cleared_content, microcompact
from app.harness.executor.registry import ToolRegistry
from tests.harness.conftest import make_ctx
from tests.harness.context.helpers import conversation, round_, system, user
from tests.harness.fake_tools import SymArgs, _indicators, _prices, all_tools, spec


def registry() -> ToolRegistry:
    tools: list[ToolSpec] = [
        *all_tools(),
        ToolSpec(name="calc", description="calc", args_model=SymArgs, category="compute", fn=_indicators),
        spec("writer", _prices, SymArgs, read_only=False),
    ]
    return ToolRegistry(tools, capability_available=lambda cap: True)


def test_count_trigger_clears_all_but_four(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    msgs = conversation(9)
    before = [m.content for m in msgs]
    result = microcompact(msgs, tools=registry(), scratchpad=ctx.scratchpad)

    assert result.trigger == "count"
    assert result.cleared == 5
    tool_msgs = [m for m in result.messages if m.role == "tool"]
    assert sum(is_cleared_content(m.content) for m in tool_msgs) == 5
    assert [is_cleared_content(m.content) for m in tool_msgs[-4:]] == [False] * 4
    for m in tool_msgs[:5]:
        assert f"call_id={m.tool_call_id}" in m.content
    assert_tool_pairing(result.messages)
    assert [m.content for m in msgs] == before  # pure: input untouched
    assert all(ctx.scratchpad.is_cleared(cid) for cid in result.cleared_call_ids)
    assert result.tokens_saved > 0


def test_below_triggers_is_noop() -> None:
    result = microcompact(conversation(8), tools=registry())
    assert result.cleared == 0 and result.trigger is None


def test_token_trigger_with_few_large_results() -> None:
    msgs = [system(), user("q")]
    for i in range(5):
        msgs += round_(i, content="x" * 70_000)
    result = microcompact(msgs, tools=registry())
    assert result.trigger == "token"
    assert result.cleared == 1


def test_token_trigger_but_nothing_old_enough() -> None:
    msgs = [system(), user("q")]
    for i in range(3):
        msgs += round_(i, content="x" * 120_000)
    result = microcompact(msgs, tools=registry())
    assert result.cleared == 0 and result.trigger is None


def test_non_compactable_tools_are_kept() -> None:
    msgs = [system(), user("q")]
    for i in range(6):
        msgs += round_(i, tool="calc")  # category compute
    for i in range(6, 12):
        msgs += round_(i, tool="writer")  # not read-only
    for i in range(12, 15):
        msgs += round_(i, tool="unknown_tool")
    result = microcompact(msgs, tools=registry())
    assert result.cleared == 0


def test_already_cleared_results_are_not_recounted() -> None:
    first = microcompact(conversation(9), tools=registry())
    second = microcompact(first.messages, tools=registry())
    assert second.cleared == 0


def test_keep_recent_zero_clears_everything() -> None:
    cfg = ContextConfig(mc_keep_recent=0, mc_count_trigger=1)
    result = microcompact(conversation(2), tools=registry(), config=cfg)
    assert result.cleared == 2
