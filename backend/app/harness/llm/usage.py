"""Read real token counts off a LangChain response (never estimate here).

Ported from ``extractUsage`` in ``../dexter/src/model/llm.ts``: prefer
the normalized ``usage_metadata`` (input/output + cache details), fall
back to the raw OpenAI-style ``response_metadata.usage`` /
``token_usage``. D1 (P06) budgets context on ``input_tokens``, so this
must be the number the provider actually billed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.ports import LLMUsage
from app.harness.llm.model_catalog import ModelInfo, cost_usd


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) else 0


def extract_usage(message: Any, info: ModelInfo | None = None) -> LLMUsage:
    usage = LLMUsage()
    meta = getattr(message, "usage_metadata", None)
    if isinstance(meta, Mapping) and (meta.get("input_tokens") or meta.get("output_tokens")):
        details = meta.get("input_token_details") or {}
        usage = LLMUsage(
            input_tokens=_int(meta.get("input_tokens")),
            output_tokens=_int(meta.get("output_tokens")),
            cache_read_tokens=_int(details.get("cache_read")),
            cache_write_tokens=_int(details.get("cache_creation")),
        )
    else:
        response_meta = getattr(message, "response_metadata", None) or {}
        raw = response_meta.get("usage") or response_meta.get("token_usage") or {}
        if isinstance(raw, Mapping):
            usage = LLMUsage(
                input_tokens=_int(raw.get("prompt_tokens") or raw.get("input_tokens")),
                output_tokens=_int(raw.get("completion_tokens") or raw.get("output_tokens")),
                cache_read_tokens=_int(raw.get("cache_read_input_tokens")),
                cache_write_tokens=_int(raw.get("cache_creation_input_tokens")),
            )
    if info is not None:
        usage.cost_usd = cost_usd(info, usage)
    return usage
