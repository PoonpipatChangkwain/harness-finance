"""GET /api/config/status — which providers are configured, never their keys.

``data`` reports the data layer (P03); ``llm`` reports the deep/quick
tiers, their failover chain and every LLM provider (P04).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.domain.data.registry import get_router
from app.harness.llm.config import (
    MODEL_SUGGESTIONS,
    PROVIDERS,
    RECOMMENDED_DEEP,
    RECOMMENDED_QUICK,
    LLMTierConfig,
    is_provider_configured,
    load_llm_config,
)
from app.harness.llm.errors import LLMNotConfigured
from app.harness.runtime.services import get_services
from app.settings import get_settings

router = APIRouter(tags=["config"])

_TIERS: dict[str, str] = {
    "yfinance": "A",
    "sec_edgar": "A",
    "polymarket": "A",
    "forexfactory": "A",
    "spot_metals": "A",
    "stocktwits": "A",
    "reddit_rss": "A",
    "crypto_news": "A",
    "web_fetch": "A",
    "static": "A",
    "fred": "B",
    "alpha_vantage": "B",
    "finnhub": "B",
    "fmp": "B",
    "financial_datasets": "C",
    "x_api": "C",
    "adanos": "C",
    "exa": "C",
    "perplexity": "C",
    "tavily": "C",
    "langsearch": "C",
    "reddit_api": "C",
    "sec_api": "C",
}


def provider_tier(provider_id: str) -> str:
    return _TIERS.get(provider_id, "?")


@router.get("/config/status")
async def config_status() -> dict[str, object]:
    router_ = get_router()
    data: dict[str, object] = {}
    for provider_id, provider in router_.providers().items():
        data[provider_id] = {
            "configured": provider.is_configured(),
            "tier": _TIERS.get(provider_id, "?"),
            "capabilities": sorted(c.value for c in provider.capabilities),
        }
    for provider_id, tier in _TIERS.items():
        data.setdefault(provider_id, {"configured": False, "tier": tier, "capabilities": []})
    return {"data": data, "llm": _llm_status()}


def _llm_status() -> dict[str, object]:
    # the app's own settings: env/.env + the Settings-page overrides (keys re-read when enabled)
    settings = get_services().llm_settings() if _services_ready() else get_settings()

    def entry(cfg: LLMTierConfig) -> dict[str, object]:
        return {
            "provider": cfg.provider,
            "model": cfg.model,
            "configured": is_provider_configured(cfg.provider, settings),
        }

    try:
        config = load_llm_config(settings)
    except (ValueError, LLMNotConfigured) as exc:
        return {"error": str(exc)}
    return {
        "deep": entry(config.deep.primary),
        "quick": entry(config.quick.primary),
        "failover": {
            "deep": [entry(c) for c in config.deep.failover],
            "quick": [entry(c) for c in config.quick.failover],
        },
        "providers": {
            pid: {"label": info.label, "configured": is_provider_configured(pid, settings)}
            for pid, info in PROVIDERS.items()
        },
    }


def _services_ready() -> bool:
    from app.harness.runtime import services as services_module

    return services_module._services is not None


@router.get("/llm/providers")
async def llm_providers() -> dict[str, object]:
    """Model picker data: every LLM provider, whether its key is set (re-read from .env, the
    key itself never leaves the server), the env var to set, and suggested models."""
    settings = get_services().llm_settings()
    default = _llm_status()
    providers = [
        {
            "id": pid,
            "label": info.label,
            "configured": is_provider_configured(pid, settings),
            "env_hint": info.env_hint,
            "free": info.free,
            "recommended": {"quick": RECOMMENDED_QUICK.get(pid), "deep": RECOMMENDED_DEEP.get(pid)},
            "models": MODEL_SUGGESTIONS.get(pid, []),
        }
        for pid, info in PROVIDERS.items()
    ]
    return {
        "default": None if "error" in default else {"deep": default["deep"], "quick": default["quick"]},
        "providers": providers,
    }
