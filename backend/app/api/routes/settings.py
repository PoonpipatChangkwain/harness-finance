"""GET/PUT /api/settings — the Settings page (P13 §13.4, contracts.md §11).

GET returns the effective values, the env/built-in defaults, the stored overrides and
the catalogs the page picks from (LLM providers + models, data providers per
capability). PUT replaces the given sections (``{}`` = back to defaults) and applies
them to the next run. API keys are never accepted or returned: only whether each
provider is configured.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.routes.config import provider_tier
from app.domain.data.base import Capability
from app.domain.data.router import DataRouter
from app.harness.config.overrides import (
    LIMIT_FIELDS,
    POLICY_FIELDS,
    SECTIONS,
    DataOverrides,
    LimitsOverrides,
    LlmOverrides,
    Overrides,
    PolicyOverrides,
    apply_to_settings,
    section_dump,
    validate_overrides,
)
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
from app.harness.storage import settings_repo
from app.settings import Settings

router = APIRouter(tags=["settings"])

# Built-in policy defaults of the workflows when no override is set (None = per risk profile).
POLICY_BUILTIN: dict[str, float | bool | None] = {
    "max_position_weight": 0.20,
    "max_sector_weight": None,
    "max_risk_per_trade": 0.02,
    "min_confidence": 0.7,
    "block_on_low_confidence": True,
    "min_risk_reward": 1.0,
    "require_stop_loss": True,
    "max_turnover": None,
}


class SettingsUpdate(BaseModel):
    llm: LlmOverrides | None = None
    data: DataOverrides | None = None
    limits: LimitsOverrides | None = None
    policy: PolicyOverrides | None = None


def _llm_values(settings: Settings, keys: Settings) -> dict[str, Any]:
    def entry(cfg: LLMTierConfig) -> dict[str, Any]:
        return {
            "provider": cfg.provider,
            "model": cfg.model,
            "configured": is_provider_configured(cfg.provider, keys),
        }

    try:
        config = load_llm_config(settings)
    except (ValueError, LLMNotConfigured) as exc:
        return {"error": str(exc), "deep": None, "quick": None, "failover": []}
    return {
        "deep": entry(config.deep.primary),
        "quick": entry(config.quick.primary),
        "failover": [entry(c) for c in config.deep.failover],
    }


def _values(settings: Settings, keys: Settings, orders: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "llm": _llm_values(settings, keys),
        "data": {"order": orders},
        "limits": {k: getattr(settings, f) for k, f in LIMIT_FIELDS.items()},
        "policy": {k: getattr(settings, f) for k, f in POLICY_FIELDS.items()},
    }


def _data_catalog(router_: DataRouter) -> dict[str, Any]:
    providers = router_.providers()
    out: dict[str, Any] = {}
    for cap in Capability:
        mode, _ = router_.default_order(cap)
        out[cap.value] = {
            "mode": mode,
            "providers": [
                {
                    "id": pid,
                    "registered": pid in providers,
                    "configured": providers[pid].is_configured() if pid in providers else False,
                    "tier": provider_tier(pid),
                }
                for pid in router_.allowed_providers(cap)
            ],
        }
    return out


def settings_view() -> dict[str, Any]:
    services = get_services()
    router_ = services.router()
    keys = services.llm_settings()
    current = services.overrides.current
    defaults_order = {cap.value: router_.default_order(cap)[1] for cap in Capability}
    effective_order = {**defaults_order, **current.data.order}
    return {
        "values": _values(services.settings, keys, effective_order),
        "defaults": _values(services.overrides.baseline, keys, defaults_order),
        "policy_builtin": POLICY_BUILTIN,
        "overrides": {s: section_dump(current, s) for s in SECTIONS},
        "catalog": {
            "llm": [
                {
                    "id": pid,
                    "label": info.label,
                    "configured": is_provider_configured(pid, keys),
                    "env_hint": info.env_hint,
                    "free": info.free,
                    "recommended": {"quick": RECOMMENDED_QUICK.get(pid), "deep": RECOMMENDED_DEEP.get(pid)},
                    "models": MODEL_SUGGESTIONS.get(pid, []),
                }
                for pid, info in PROVIDERS.items()
            ],
            "data": _data_catalog(router_),
        },
    }


@router.get("/settings")
async def get_settings_view() -> dict[str, Any]:
    return settings_view()


@router.put("/settings")
async def put_settings(body: SettingsUpdate) -> dict[str, Any]:
    services = get_services()
    given = body.model_dump(exclude_unset=True)
    merged = Overrides(
        **{
            s: getattr(body, s)
            if s in given and getattr(body, s) is not None
            else getattr(services.overrides.current, s)
            for s in SECTIONS
        }
    )
    errors = validate_overrides(merged, services.router())
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    if merged.llm != services.overrides.current.llm:
        trial = services.settings.model_copy()
        apply_to_settings(trial, services.overrides.baseline, merged)
        try:
            load_llm_config(trial)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=[f"llm: {exc}"]) from exc
        except LLMNotConfigured:
            pass  # no key yet: the page shows the tier as not configured
    for section in SECTIONS:
        if section in given and getattr(body, section) is not None:
            await settings_repo.save_section(section, section_dump(merged, section))
    services.apply_overrides(merged)
    return settings_view()
