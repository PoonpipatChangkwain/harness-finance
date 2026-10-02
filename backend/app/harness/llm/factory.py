"""create_client(cfg) → BaseLLMClient, importing provider SDKs lazily.

Ported from ``../TradingAgents/tradingagents/llm_clients/factory.py``:
native APIs (Anthropic, Google AI Studio) are matched first; everything
else is OpenAI-compatible (OpenAI, xAI Grok, ThaiLLM, Ollama, DeepSeek,
OpenRouter) and goes through ``ChatOpenAI`` with a ``base_url``. A
provider without credentials raises ``LLMNotConfigured`` before any SDK
is touched.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from app.harness.llm.base import BaseLLMClient
from app.harness.llm.config import PROVIDERS, LLMTierConfig, canonical_provider, resolve_endpoint
from app.harness.llm.errors import LLMNotConfigured
from app.harness.llm.model_catalog import ModelInfo, lookup, parse_pricing
from app.settings import Settings, get_settings

DEFAULT_MAX_TOKENS = 16_000


def _build_chat_model(cfg: LLMTierConfig, info: ModelInfo, settings: Settings) -> BaseChatModel:
    provider = PROVIDERS[canonical_provider(cfg.provider)]
    endpoint = resolve_endpoint(provider.id, settings)
    temperature = cfg.temperature if info.supports_temperature else None
    kwargs: dict[str, Any] = {"model": cfg.model, "timeout": cfg.timeout_s}

    try:
        if provider.kind == "anthropic":
            from langchain_anthropic import ChatAnthropic

            kwargs.update(
                api_key=endpoint.api_key, max_tokens=cfg.max_tokens or DEFAULT_MAX_TOKENS, max_retries=0
            )
            if temperature is not None:
                kwargs["temperature"] = temperature
            if cfg.effort:
                kwargs["effort"] = cfg.effort
            return ChatAnthropic(**kwargs)

        if provider.kind == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI

            kwargs.update(api_key=endpoint.api_key, max_retries=0)
            if cfg.max_tokens:
                kwargs["max_output_tokens"] = cfg.max_tokens
            if temperature is not None:
                kwargs["temperature"] = temperature
            if cfg.effort:
                kwargs["thinking_level"] = cfg.effort
            return ChatGoogleGenerativeAI(**kwargs)

        from langchain_openai import ChatOpenAI

        # Ollama is keyless but the OpenAI client insists on a non-empty key.
        kwargs.update(api_key=endpoint.api_key or "not-needed", max_retries=0, stream_usage=True)
        if endpoint.base_url:
            kwargs["base_url"] = endpoint.base_url
        if cfg.max_tokens:
            kwargs["max_tokens"] = cfg.max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature
        if cfg.effort:
            kwargs["reasoning_effort"] = cfg.effort
        return ChatOpenAI(**kwargs)
    except ImportError as exc:  # pragma: no cover - deps are pinned in pyproject
        raise LLMNotConfigured(
            f"SDK for {provider.label} is not installed: {exc}", provider=provider.id
        ) from exc


def create_client(cfg: LLMTierConfig, settings: Settings | None = None) -> BaseLLMClient:
    settings = settings or get_settings()
    provider = PROVIDERS[canonical_provider(cfg.provider)]
    info = lookup(cfg.model, free=provider.free, overrides=parse_pricing(settings.llm_pricing))
    chat_model = _build_chat_model(cfg, info, settings)
    return BaseLLMClient(cfg, provider, chat_model, info)
