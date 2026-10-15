"""Context window + price per 1M tokens for known models.

Ported concept from ``../TradingAgents/tradingagents/llm_clients/model_catalog.py``.
Used for ``cost_usd`` (llm.finished) and the compaction threshold (P06).
Anthropic prices are the first-party API rates. Other vendors change
prices often, so they are not hard-coded: set ``LLM_PRICING`` in .env
(``model=in/out[/cache_read]``) to get real costs; an unknown model
falls back to a 128k window and $0 with a one-time warning.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from app.domain.ports import LLMUsage

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_WINDOW = 128_000


class ModelInfo(BaseModel):
    context_window: int = DEFAULT_CONTEXT_WINDOW
    input_per_m: float = 0.0
    output_per_m: float = 0.0
    cache_read_per_m: float | None = None  # None → 10% of input
    cache_write_per_m: float | None = None  # None → 125% of input
    # Claude 5-family / Opus 4.7+ reject temperature/top_p (400).
    supports_temperature: bool = True
    known: bool = True


def _claude(
    window: int, inp: float, out: float, cache_read: float | None = None, *, sampling: bool
) -> ModelInfo:
    return ModelInfo(
        context_window=window,
        input_per_m=inp,
        output_per_m=out,
        cache_read_per_m=cache_read,
        supports_temperature=sampling,
    )


# Keys are matched exactly first, then as a prefix (so dated snapshots such as
# "claude-haiku-4-5-20251001" resolve to "claude-haiku-4-5").
CATALOG: dict[str, ModelInfo] = {
    "claude-fable-5-1": _claude(1_000_000, 10.0, 50.0, 0.25, sampling=False),
    "claude-fable-5": _claude(1_000_000, 10.0, 50.0, sampling=False),
    "claude-opus-5-5": _claude(1_000_000, 4.0, 20.0, 0.20, sampling=False),
    "claude-opus-5": _claude(1_000_000, 5.0, 25.0, sampling=False),
    "claude-opus-4-8": _claude(1_000_000, 5.0, 25.0, sampling=False),
    "claude-opus-4-7": _claude(1_000_000, 5.0, 25.0, sampling=False),
    "claude-opus-4-6": _claude(1_000_000, 5.0, 25.0, sampling=True),
    "claude-sonnet-5-5": _claude(1_000_000, 2.0, 10.0, 0.20, sampling=False),
    "claude-sonnet-5": _claude(1_000_000, 2.0, 10.0, sampling=False),
    "claude-sonnet-4-6": _claude(1_000_000, 3.0, 15.0, sampling=True),
    # Claude Code 2.1.91 reports contextWindow=200000 for claude-haiku-5-5 (modelUsage)
    "claude-haiku-5-5": _claude(200_000, 0.10, 0.50, sampling=False),
    "claude-haiku-4-5": _claude(200_000, 1.0, 5.0, sampling=True),
    # window only (price via LLM_PRICING): keeps the compaction threshold right
    "openai/gpt-oss-120b": ModelInfo(context_window=131_072),
    "openai/gpt-oss-20b": ModelInfo(context_window=131_072),
    "gpt-oss:120b": ModelInfo(context_window=131_072),  # Ollama Cloud names
    "gpt-oss:20b": ModelInfo(context_window=131_072),
    "gemma4:31b": ModelInfo(context_window=131_072),
    # ThaiLLM (free): the 400 error reports "maximum context length is 16384 tokens"
    "Typhoon-S-ThaiLLM-8B-Instruct": ModelInfo(context_window=16_384),
}

# Model aliases the Claude Code CLI accepts (``--model opus``) → the latest id, for pricing.
MODEL_ALIASES: dict[str, str] = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
    "fable": "claude-fable-5-1",
}

_warned: set[str] = set()


def parse_pricing(spec: str) -> dict[str, ModelInfo]:
    out: dict[str, ModelInfo] = {}
    for item in (spec or "").split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        model, _, prices = item.partition("=")
        parts = [float(p) for p in prices.split("/") if p.strip()]
        if len(parts) < 2:
            raise ValueError(f"bad LLM_PRICING entry {item!r}; expected model=in/out[/cache_read]")
        out[model.strip()] = ModelInfo(
            input_per_m=parts[0], output_per_m=parts[1], cache_read_per_m=parts[2] if len(parts) > 2 else None
        )
    return out


def lookup(model: str, *, free: bool = False, overrides: dict[str, ModelInfo] | None = None) -> ModelInfo:
    if overrides and model in overrides:
        return overrides[model]
    model = MODEL_ALIASES.get(model.strip().lower(), model)
    if model in CATALOG:
        return CATALOG[model]
    for key in sorted(CATALOG, key=len, reverse=True):
        if model.startswith(key):
            return CATALOG[key]
    if not free and model not in _warned:
        _warned.add(model)
        logger.warning(
            "model %r not in catalog: assuming %d-token window and $0 cost (set LLM_PRICING to fix)",
            model,
            DEFAULT_CONTEXT_WINDOW,
        )
    return ModelInfo(known=free)


def cost_usd(info: ModelInfo, usage: LLMUsage) -> float:
    """Cost of one call. ``usage.input_tokens`` includes cache reads/writes (as the APIs report it)."""
    cache_read_rate = info.cache_read_per_m if info.cache_read_per_m is not None else info.input_per_m * 0.1
    cache_write_rate = (
        info.cache_write_per_m if info.cache_write_per_m is not None else info.input_per_m * 1.25
    )
    uncached = max(0, usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens)
    total = (
        uncached * info.input_per_m
        + usage.cache_read_tokens * cache_read_rate
        + usage.cache_write_tokens * cache_write_rate
        + usage.output_tokens * info.output_per_m
    )
    return round(total / 1_000_000, 8)
