from __future__ import annotations

from pathlib import Path

from app.domain.ports import LLMMessage, LLMUsage, ToolCallReq
from app.harness.context.config import ContextConfig
from app.harness.context.tokens import (
    TokenCounter,
    compact_threshold,
    context_window,
    current_context_tokens,
    estimate_messages_tokens,
    estimate_tokens,
    message_text,
    usage_percent,
)
from app.settings import Settings
from tests.harness.conftest import make_ctx


def test_estimate_tokens_uses_3_5_chars_per_token() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 7) == 2
    assert estimate_tokens("a" * 8) == 3


def test_message_text_includes_tool_calls() -> None:
    msg = LLMMessage(
        role="assistant", content="hi", tool_calls=[ToolCallReq(id="c1", name="t", args={"a": 1})]
    )
    assert "c1" in message_text(msg) and message_text(LLMMessage(role="user", content="x")) == "x"
    assert estimate_messages_tokens([msg]) > 0


def test_threshold_known_and_unknown_model() -> None:
    assert context_window("claude-haiku-4-5") == 200_000
    assert compact_threshold("claude-haiku-4-5") == 167_000
    assert compact_threshold("mystery-model-x") == 95_000


def test_small_window_caps_the_reserve() -> None:
    assert context_window("Typhoon-S-ThaiLLM-8B-Instruct") == 16_384
    threshold = compact_threshold("Typhoon-S-ThaiLLM-8B-Instruct")
    assert 0 < threshold < 16_384 and threshold == 16_384 - int(16_384 * 0.35)


def test_threshold_respects_config() -> None:
    cfg = ContextConfig(
        autocompact_buffer_tokens=0, max_output_tokens_for_summary=0, default_context_window=50_000
    )
    assert compact_threshold("mystery-model-y", cfg) == 50_000


def test_token_counter_by_purpose_and_last_api() -> None:
    tc = TokenCounter()
    tc.observe(LLMUsage(input_tokens=1000, output_tokens=10, cost_usd=0.1))
    tc.observe(LLMUsage(input_tokens=50_000, output_tokens=5), "compact")
    assert tc.last_api_input_tokens == 1000  # compact calls don't measure the agent context
    assert tc.by_purpose["compact"].input_tokens == 50_000
    assert tc.total().input_tokens == 51_000
    tc.discount(300)
    assert tc.last_api_input_tokens == 700
    tc.discount(10_000)
    assert tc.last_api_input_tokens == 0
    tc.invalidate()
    assert tc.last_api_input_tokens is None
    tc.discount(5)
    assert tc.last_api_input_tokens is None


def test_current_context_tokens_prefers_api_value(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    msgs = [LLMMessage(role="user", content="x" * 350)]
    assert current_context_tokens(ctx, msgs) == 100
    ctx.record_llm_usage(LLMUsage(input_tokens=4242))
    assert current_context_tokens(ctx, msgs) == 4242
    assert ctx.usage.by_purpose["agent"]["calls"] == 1


def test_usage_percent() -> None:
    assert usage_percent(50, 200) == 25.0
    assert usage_percent(500, 200) == 100.0
    assert usage_percent(1, 0) == 0.0


def test_config_from_settings(tmp_path: Path) -> None:
    s = Settings(_env_file=None, data_dir=tmp_path, context_mc_count_trigger=2, context_memory_flush=False)  # type: ignore[call-arg]
    cfg = ContextConfig.from_settings(s, keep_tool_uses=7)
    assert cfg.mc_count_trigger == 2 and cfg.memory_flush is False and cfg.keep_tool_uses == 7
    assert cfg.mc_keep_recent == 4


def test_config_claude_code_has_its_own_count_trigger(tmp_path: Path) -> None:
    s = Settings(_env_file=None, data_dir=tmp_path, context_mc_count_trigger=2)  # type: ignore[call-arg]
    assert ContextConfig.from_settings(s, provider="claude_code").mc_count_trigger is None  # off by default
    assert ContextConfig.from_settings(s, provider="anthropic").mc_count_trigger == 2
    assert ContextConfig.from_settings(s).mc_count_trigger == 2
    s = Settings(_env_file=None, data_dir=tmp_path, claude_code_mc_count_trigger=30)  # type: ignore[call-arg]
    assert ContextConfig.from_settings(s, provider="claude_code").mc_count_trigger == 30
    assert ContextConfig.from_settings(s, provider="claude_code", mc_count_trigger=5).mc_count_trigger == 5
