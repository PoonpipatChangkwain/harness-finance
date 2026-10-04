"""Settings-page overrides (P13 §13.4) applied on top of the env/.env defaults.

Four sections, stored one row each in the ``settings`` table:

- ``llm``     deep / quick tier (provider + model) and the failover chain
- ``data``    provider order per capability (the data router's failover/fanout order)
- ``limits``  run guards (D5) and compaction thresholds (D1)
- ``policy``  gate limits used by the trading and portfolio-advisor policies (T4)

Overrides are applied to the live ``Settings`` object and the data router, so the next
run picks them up. API keys are never part of this: they live only in env/.env and the
API reports just whether each one is set.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.data.base import Capability
from app.domain.data.router import DataRouter
from app.harness.llm.config import canonical_provider
from app.settings import Settings

SECTIONS = ("llm", "data", "limits", "policy")


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LlmTierChoice(_Section):
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=200)


class LlmOverrides(_Section):
    deep: LlmTierChoice | None = None
    quick: LlmTierChoice | None = None
    # applies to both tiers; [] = no failover, None = the env default
    failover: list[LlmTierChoice] | None = Field(default=None, max_length=10)


class DataOverrides(_Section):
    order: dict[str, list[str]] = Field(default_factory=dict)


class LimitsOverrides(_Section):
    max_iterations: int | None = Field(default=None, ge=1, le=50)
    max_tool_calls_total: int | None = Field(default=None, ge=1, le=500)
    max_calls_per_tool: int | None = Field(default=None, ge=1, le=20)
    similarity_threshold: float | None = Field(default=None, ge=0.1, le=1.0)
    tool_timeout_s: float | None = Field(default=None, ge=1, le=600)
    run_wall_clock_s: float | None = Field(default=None, ge=10, le=7200)
    max_cost_usd: float | None = Field(default=None, ge=0, le=100)
    mc_count_trigger: int | None = Field(default=None, ge=1, le=100)
    mc_keep_recent: int | None = Field(default=None, ge=0, le=50)
    mc_token_trigger: int | None = Field(default=None, ge=1_000, le=2_000_000)
    autocompact_buffer_tokens: int | None = Field(default=None, ge=0, le=200_000)


class PolicyOverrides(_Section):
    max_position_weight: float | None = Field(default=None, gt=0, le=1)
    max_sector_weight: float | None = Field(default=None, gt=0, le=1)
    max_risk_per_trade: float | None = Field(default=None, gt=0, le=0.5)
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    block_on_low_confidence: bool | None = None
    min_risk_reward: float | None = Field(default=None, ge=0, le=10)
    min_counter_trend_risk_reward: float | None = Field(default=None, ge=0, le=10)
    require_stop_loss: bool | None = None
    max_turnover: float | None = Field(default=None, gt=0, le=2)


class Overrides(_Section):
    llm: LlmOverrides = Field(default_factory=LlmOverrides)
    data: DataOverrides = Field(default_factory=DataOverrides)
    limits: LimitsOverrides = Field(default_factory=LimitsOverrides)
    policy: PolicyOverrides = Field(default_factory=PolicyOverrides)


LIMIT_FIELDS: dict[str, str] = {
    "max_iterations": "run_max_iterations",
    "max_tool_calls_total": "run_max_tool_calls_total",
    "max_calls_per_tool": "run_max_calls_per_tool",
    "similarity_threshold": "run_similarity_threshold",
    "tool_timeout_s": "run_tool_timeout_s",
    "run_wall_clock_s": "run_wall_clock_s",
    "max_cost_usd": "run_max_cost_usd",
    "mc_count_trigger": "context_mc_count_trigger",
    "mc_keep_recent": "context_mc_keep_recent",
    "mc_token_trigger": "context_mc_token_trigger",
    "autocompact_buffer_tokens": "context_autocompact_buffer_tokens",
}
POLICY_FIELDS: dict[str, str] = {k: f"policy_{k}" for k in PolicyOverrides.model_fields}
_LLM_FIELDS = (
    "llm_deep_provider", "llm_deep_model", "llm_deep_effort",
    "llm_quick_provider", "llm_quick_model", "llm_quick_effort",
    "llm_failover", "llm_deep_failover", "llm_quick_failover",
)  # fmt: skip


def section_dump(overrides: Overrides, section: str) -> dict[str, Any]:
    """What gets stored for one section: only the fields the user set."""
    model: BaseModel = getattr(overrides, section)
    dumped = model.model_dump(mode="json", exclude_none=True)
    if section == "data" and not dumped.get("order"):
        return {}
    return dumped


def validate_overrides(overrides: Overrides, router: DataRouter) -> list[str]:
    errors: list[str] = []
    llm = overrides.llm
    for label, choice in (("deep", llm.deep), ("quick", llm.quick)):
        if choice is not None:
            try:
                canonical_provider(choice.provider)
            except ValueError as exc:
                errors.append(f"llm.{label}: {exc}")
    for i, choice in enumerate(llm.failover or []):
        try:
            canonical_provider(choice.provider)
        except ValueError as exc:
            errors.append(f"llm.failover[{i}]: {exc}")
    known = {c.value: c for c in Capability}
    for cap_name, order in overrides.data.order.items():
        cap = known.get(cap_name)
        if cap is None:
            errors.append(f"data.order: unknown capability {cap_name!r}")
            continue
        if not order:
            errors.append(f"data.order.{cap_name}: needs at least one provider")
        if len(set(order)) != len(order):
            errors.append(f"data.order.{cap_name}: duplicate provider")
        allowed = set(router.allowed_providers(cap))
        unknown = [p for p in order if p not in allowed]
        if unknown:
            errors.append(f"data.order.{cap_name}: {', '.join(unknown)} cannot serve {cap_name}")
    return errors


def apply_to_settings(target: Settings, baseline: Settings, overrides: Overrides) -> None:
    """Set every overridable field of ``target``: the override when given, else ``baseline``'s value."""
    for key, field in LIMIT_FIELDS.items():
        value = getattr(overrides.limits, key)
        setattr(target, field, value if value is not None else getattr(baseline, field))
    for key, field in POLICY_FIELDS.items():
        value = getattr(overrides.policy, key)
        setattr(target, field, value if value is not None else getattr(baseline, field))
    for field in _LLM_FIELDS:
        setattr(target, field, getattr(baseline, field))
    llm = overrides.llm
    for tier in ("deep", "quick"):
        choice: LlmTierChoice | None = getattr(llm, tier)
        if choice is not None:
            setattr(target, f"llm_{tier}_provider", canonical_provider(choice.provider))
            setattr(target, f"llm_{tier}_model", choice.model.strip())
            setattr(target, f"llm_{tier}_effort", None)
    if llm.failover is not None:
        target.llm_failover = ",".join(
            f"{canonical_provider(c.provider)}:{c.model.strip()}" for c in llm.failover
        )
        target.llm_deep_failover = None
        target.llm_quick_failover = None


def trading_policy_config(settings: Settings) -> dict[str, Any]:
    """Workflow config keys the trading policy/sizing read (None = keep the built-in default)."""
    out: dict[str, Any] = {
        "require_stop_loss": settings.policy_require_stop_loss,
        "block_on_low_confidence": settings.policy_block_on_low_confidence,
    }
    for key in (
        "max_position_weight",
        "max_risk_per_trade",
        "min_confidence",
        "min_risk_reward",
        "min_counter_trend_risk_reward",
    ):
        value = getattr(settings, f"policy_{key}")
        if value is not None:
            out[key] = value
    return out


def advisor_policy_config(settings: Settings) -> dict[str, Any]:
    """Settings limits for the advisor: they replace the risk-profile defaults (a run's own
    constraints still win)."""
    limits = {
        "max_weight": settings.policy_max_position_weight,
        "max_sector": settings.policy_max_sector_weight,
        "max_turnover": settings.policy_max_turnover,
    }
    out: dict[str, Any] = {"limit_overrides": {k: v for k, v in limits.items() if v is not None}}
    if settings.policy_min_confidence is not None:
        out["min_confidence"] = settings.policy_min_confidence
    return out


class SettingsOverrides:
    """The overrides in effect + the env baseline they are applied on (one per ``HarnessServices``)."""

    def __init__(self, settings: Settings) -> None:
        self.baseline = settings.model_copy()
        self.current = Overrides()

    def apply(self, settings: Settings, overrides: Overrides, router: DataRouter) -> None:
        self.current = overrides
        apply_to_settings(settings, self.baseline, overrides)
        router.set_order_overrides(overrides.data.order)

    def on_fresh(self, fresh: Settings) -> Settings:
        """A settings object re-read from env (new API keys) with the current overrides on top."""
        apply_to_settings(fresh, fresh.model_copy(), self.current)
        return fresh
