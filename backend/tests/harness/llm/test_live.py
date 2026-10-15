"""One real call per configured provider. Run with ``uv run pytest -m live``."""

from __future__ import annotations

import os

import pytest

from app.domain.ports import LLMMessage
from app.harness.llm.adapter import HarnessLLM
from app.harness.llm.config import (
    RECOMMENDED_QUICK,
    LLMConfig,
    LLMTierConfig,
    TierChain,
    is_provider_configured,
)
from app.settings import get_settings

# Override per provider with LLM_LIVE_<PROVIDER>_MODEL if these defaults are not on your account.
# Reuses the same recommended-model table as the quick-tier auto-pick (config.py),
# so this stays the one place that names "the" default model per provider.
_DEFAULT_MODELS = RECOMMENDED_QUICK


@pytest.mark.live
@pytest.mark.parametrize("provider", sorted(_DEFAULT_MODELS))
async def test_live_provider(provider: str) -> None:
    settings = get_settings()
    if not is_provider_configured(provider, settings):
        pytest.skip(f"{provider} not configured")
    model = os.environ.get(f"LLM_LIVE_{provider.upper()}_MODEL", _DEFAULT_MODELS[provider])
    chain = TierChain(primary=LLMTierConfig(provider=provider, model=model, timeout_s=60))
    llm = HarnessLLM.from_config(LLMConfig(deep=chain, quick=chain), settings)
    resp = await llm.complete(
        [LLMMessage(role="user", content="Reply with the single word: pong")], tier="quick", purpose="agent"
    )
    assert resp.text.strip()
    assert resp.usage.input_tokens > 0


@pytest.mark.live
async def test_live_claude_code_structured() -> None:
    """P16: the local Claude Code CLI (subscription) answers a structured call natively."""
    from pydantic import BaseModel

    class Count(BaseModel):
        n: int

    settings = get_settings()
    if not is_provider_configured("claude_code", settings):
        pytest.skip("claude_code not configured (no Claude Code CLI, or no token/login)")
    chain = TierChain(primary=LLMTierConfig(provider="claude_code", model="claude-haiku-5-5", timeout_s=120))
    llm = HarnessLLM.from_config(LLMConfig(deep=chain, quick=chain), settings)
    result = await llm.structured(
        [LLMMessage(role="user", content="How many legs does a spider have? Answer as n.")],
        Count,
        tier="quick",
        purpose="agent",
    )
    assert result.mode == "native", result.raw_text
    assert result.value == Count(n=8)
