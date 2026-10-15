"""LLM provider registry + deep/quick tier config (F4).

Ported from ``../TradingAgents/tradingagents/llm_clients/api_key_env.py``
(provider → key env) and ``../dexter/src/providers.ts`` (provider table
with base URLs). Three native APIs (Anthropic, Google AI Studio, OpenAI)
plus OpenAI-compatible endpoints (xAI Grok, ThaiLLM, Ollama, DeepSeek,
Groq, OpenRouter) that reuse the OpenAI client with a ``base_url``.
(P16) ``claude_code`` is Claude through the user's own subscription, by
spawning the locally installed Claude Code CLI (``claude_code.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.ports import Tier
from app.harness.llm.errors import LLMNotConfigured
from app.settings import Settings

ClientKind = Literal["anthropic", "google", "openai", "claude_code"]
StructuredMethod = Literal["json_schema", "function_calling"]


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    kind: ClientKind
    key_setting: str | None  # Settings attribute holding the API key (None = keyless)
    base_url: str | None = None  # fixed default endpoint
    base_url_setting: str | None = None  # Settings attribute that overrides / supplies the endpoint
    base_url_required: bool = False
    free: bool = False  # local / free endpoint: cost 0 is expected, not a catalog miss
    structured_method: StructuredMethod = "json_schema"
    env_hint: str = ""
    key_optional: bool = False  # a missing key is not "not configured" (claude_code: local login)
    note: str | None = None  # shown under the provider in the Settings page


PROVIDERS: dict[str, ProviderInfo] = {
    p.id: p
    for p in (
        ProviderInfo(
            "anthropic", "Anthropic (Claude)", "anthropic", "anthropic_api_key", env_hint="ANTHROPIC_API_KEY"
        ),
        ProviderInfo(
            "google",
            "Google AI Studio (Gemini)",
            "google",
            "google_api_key",
            env_hint="GOOGLE_API_KEY or GEMINI_API_KEY",
        ),
        ProviderInfo(
            "claude_code",
            "Claude (subscription via Claude Code)",
            "claude_code",
            "anthropic_api_sub_key",
            # billed to the user's subscription, not per token (cost still comes from the catalog)
            free=True,
            structured_method="json_schema",
            env_hint="ANTHROPIC_API_SUB_KEY (claude setup-token) + Claude Code CLI",
            key_optional=True,
            note="ใช้ subscription ของคุณผ่าน Claude Code CLI ที่ติดตั้งในเครื่อง — สำหรับใช้ส่วนตัวเท่านั้น",
        ),
        ProviderInfo("openai", "OpenAI (ChatGPT)", "openai", "openai_api_key", env_hint="OPENAI_API_KEY"),
        ProviderInfo(
            "xai",
            "xAI (Grok)",
            "openai",
            "xai_api_key",
            base_url="https://api.x.ai/v1",
            env_hint="XAI_API_KEY",
        ),
        ProviderInfo(
            "thaillm",
            "ThaiLLM",
            "openai",
            "thaillm_api_key",
            # vLLM behind Cloudflare; all four ThaiLLM-8B models support tool calls
            # (the extra qwen3.x models on it have tool calls disabled).
            base_url="https://thaillm.or.th/api/v1",
            base_url_setting="thaillm_base_url",
            free=True,
            structured_method="function_calling",
            env_hint="THAILLM_API_KEY",
        ),
        ProviderInfo(
            "ollama",
            "Ollama (local)",
            "openai",
            None,
            base_url_setting="ollama_base_url",
            base_url_required=True,
            free=True,
            structured_method="function_calling",
            env_hint="OLLAMA_BASE_URL (e.g. http://localhost:11434)",
        ),
        ProviderInfo(
            "ollama_cloud",
            "Ollama Cloud",
            "openai",
            "ollama_api_key",
            # ollama.com hosted models (subscription/free tier, not billed per token)
            base_url="https://ollama.com/v1",
            free=True,
            structured_method="function_calling",
            env_hint="OLLAMA_API_KEY",
        ),
        ProviderInfo(
            "deepseek",
            "DeepSeek",
            "openai",
            "deepseek_api_key",
            base_url="https://api.deepseek.com",
            structured_method="function_calling",
            env_hint="DEEPSEEK_API_KEY",
        ),
        ProviderInfo(
            "groq",
            "Groq",
            "openai",
            "groq_api_key",
            # fast LPU inference with a free tier; openai/gpt-oss-120b supports tool calls
            base_url="https://api.groq.com/openai/v1",
            structured_method="function_calling",
            env_hint="GROQ_API_KEY",
        ),
        ProviderInfo(
            "openrouter",
            "OpenRouter",
            "openai",
            "openrouter_api_key",
            base_url="https://openrouter.ai/api/v1",
            structured_method="function_calling",
            env_hint="OPENROUTER_API_KEY",
        ),
    )
}

# Recommended model per provider, used to auto-pick a tier when the caller
# (env, UI) didn't choose a provider — and reused as the known-good default
# in tests/smoke, so there is one place that names "the" ThaiLLM model etc.
# Priority order = which provider auto-pick tries first: native paid APIs
# (best quality per call), then the free OpenAI-compatible ones.
AUTO_PICK_ORDER: tuple[str, ...] = (
    "anthropic",
    "claude_code",
    "google",
    "openai",
    "groq",
    "thaillm",
    "ollama_cloud",
    "ollama",
    "deepseek",
    "xai",
    "openrouter",
)

RECOMMENDED_QUICK: dict[str, str] = {
    "anthropic": "claude-haiku-4-5",
    "claude_code": "claude-haiku-5-5",
    "google": "gemini-3.8-flash",  # 2.5-flash is closed to new AI Studio keys; 3.8 ~8x faster than 3.5
    "openai": "gpt-4o-mini",
    "xai": "grok-3-mini",
    # Typhoon-S: fastest (~1s) and most reliable at tool calls of the 4 free
    # ThaiLLM-8B models (checked by hand against get_prices/compute_indicators).
    "thaillm": "Typhoon-S-ThaiLLM-8B-Instruct",
    "ollama": "llama3.1",
    "ollama_cloud": "gpt-oss:120b",
    "deepseek": "deepseek-chat",
    "groq": "openai/gpt-oss-120b",
    "openrouter": "openrouter/auto",
}
RECOMMENDED_DEEP: dict[str, str] = {
    **RECOMMENDED_QUICK,
    "anthropic": "claude-sonnet-5",
    "claude_code": "claude-opus-5-5",
    # Thinking model; slower (~10s) but still tool-call-capable, and deep
    # calls tolerate the extra latency.
    "thaillm": "Pathumma-ThaiLLM-qwen3-8b-think-3.0.0",
}


# Models offered in the UI model picker per provider (the recommended ones
# first). The picker also accepts any other model id the provider serves.
MODEL_SUGGESTIONS: dict[str, list[str]] = {
    "anthropic": ["claude-sonnet-5", "claude-haiku-4-5", "claude-opus-5-5", "claude-fable-5-1"],
    "claude_code": [
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-fable-5-1",
        "opus",
        "sonnet",
        "haiku",
    ],
    "google": ["gemini-3.8-flash", "gemini-3.5-flash"],
    "openai": ["gpt-4o-mini"],
    "xai": ["grok-3-mini"],
    "thaillm": [
        "Typhoon-S-ThaiLLM-8B-Instruct",
        "Pathumma-ThaiLLM-qwen3-8b-think-3.0.0",
        "OpenThaiGPT-ThaiLLM-8B-Instruct-v7.2",
        "THaLLE-0.2-ThaiLLM-8B-fa",
    ],
    "ollama": ["llama3.1"],
    "ollama_cloud": ["gpt-oss:120b", "gpt-oss:20b", "gemma4:31b"],
    "deepseek": ["deepseek-chat"],
    "groq": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
    "openrouter": ["openrouter/auto"],
}


def llm_config_for_choice(
    provider: str, model: str, *, timeout_s: float = 120.0, quick: TierChain | None = None
) -> LLMConfig:
    """The deep tier (agent + final answer) on one user-picked model with no failover, so a
    test of that model is not answered by another provider. The quick tier (plan, compaction)
    keeps the configured chain when given, else uses the picked model too."""
    primary = LLMTierConfig(provider=canonical_provider(provider), model=model.strip(), timeout_s=timeout_s)
    return LLMConfig(deep=TierChain(primary=primary), quick=quick or TierChain(primary=primary))


def auto_pick(settings: Settings, table: dict[str, str]) -> tuple[str, str] | None:
    """First configured provider from ``AUTO_PICK_ORDER``, paired with its recommended model."""
    for provider in AUTO_PICK_ORDER:
        if is_provider_configured(provider, settings):
            return provider, table[provider]
    return None


_ALIASES = {
    "claude": "anthropic",
    "gemini": "google",
    "aistudio": "google",
    "ai_studio": "google",
    "google_ai_studio": "google",
    "chatgpt": "openai",
    "gpt": "openai",
    "grok": "xai",
    "thai_llm": "thaillm",
    "ollama_local": "ollama",
    "ollamacloud": "ollama_cloud",
    "claude_sub": "claude_code",
    "claude_subscription": "claude_code",
    "claude_code_cli": "claude_code",
}


def canonical_provider(provider: str) -> str:
    p = provider.strip().lower().replace("-", "_")
    p = _ALIASES.get(p, p)
    if p not in PROVIDERS:
        raise ValueError(f"unsupported LLM provider {provider!r}; expected one of {sorted(PROVIDERS)}")
    return p


@dataclass(frozen=True)
class Endpoint:
    api_key: str | None
    base_url: str | None


def resolve_endpoint(provider: str, settings: Settings) -> Endpoint:
    """API key + base URL for a provider, or raise ``LLMNotConfigured``. Never logs the key."""
    info = PROVIDERS[canonical_provider(provider)]
    api_key: str | None = None
    if info.key_setting is not None:
        secret = getattr(settings, info.key_setting, None)
        api_key = secret.get_secret_value() if secret is not None else None
        if not api_key and not info.key_optional:
            raise LLMNotConfigured(f"{info.label} is not configured (set {info.env_hint})", provider=info.id)

    base_url = info.base_url
    if info.base_url_setting is not None:
        override = getattr(settings, info.base_url_setting, None)
        if override:
            base_url = str(override).rstrip("/")
    if info.base_url_required and not base_url:
        raise LLMNotConfigured(f"{info.label} is not configured (set {info.env_hint})", provider=info.id)
    if info.id == "ollama" and base_url and not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return Endpoint(api_key=api_key or None, base_url=base_url)


def provider_status(provider: str, settings: Settings) -> tuple[bool, str | None]:
    """(configured, why not) — the reason names what to set, never a key value."""
    try:
        info = PROVIDERS[canonical_provider(provider)]
        resolve_endpoint(info.id, settings)
    except ValueError as exc:
        return False, str(exc)
    except LLMNotConfigured as exc:
        return False, str(exc)
    if info.kind == "claude_code":
        from app.harness.llm.claude_code import claude_code_status  # lazy: avoids an import cycle

        return claude_code_status(settings)
    return True, None


def is_provider_configured(provider: str, settings: Settings) -> bool:
    return provider_status(provider, settings)[0]


class LLMTierConfig(BaseModel):
    provider: str
    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    effort: str | None = None
    timeout_s: float = 120.0

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


class TierChain(BaseModel):
    primary: LLMTierConfig
    failover: list[LLMTierConfig] = Field(default_factory=list)

    def chain(self) -> list[LLMTierConfig]:
        return [self.primary, *self.failover]


class LLMConfig(BaseModel):
    deep: TierChain
    quick: TierChain

    def tier(self, tier: Tier) -> TierChain:
        return self.deep if tier == "deep" else self.quick


def parse_failover(spec: str | None, *, timeout_s: float = 120.0) -> list[LLMTierConfig]:
    """``"openai:gpt-x,deepseek:deepseek-chat"`` → configs. The model may itself contain ':'."""
    out: list[LLMTierConfig] = []
    for item in (spec or "").split(","):
        item = item.strip()
        if not item:
            continue
        provider, sep, model = item.partition(":")
        if not sep or not model.strip():
            raise ValueError(f"bad LLM failover entry {item!r}; expected provider:model")
        out.append(
            LLMTierConfig(provider=canonical_provider(provider), model=model.strip(), timeout_s=timeout_s)
        )
    return out


def _tier_provider_model(
    provider: str | None, model: str | None, settings: Settings, *, tier_name: str, table: dict[str, str]
) -> tuple[str, str]:
    """Explicit provider+model, or auto-pick the first configured provider's recommended model."""
    provider = provider or None  # an env var set to "" reads as "", not unset
    model = model or None
    if provider is None and model is None:
        picked = auto_pick(settings, table)
        if picked is not None:
            return picked
        hints = ", ".join(p.env_hint for p in PROVIDERS.values())
        raise LLMNotConfigured(
            f"no LLM provider is configured for the {tier_name} tier; set one API key ({hints}) "
            f"or LLM_{tier_name.upper()}_PROVIDER + LLM_{tier_name.upper()}_MODEL"
        )
    if provider is None or model is None:
        raise ValueError(
            f"set both LLM_{tier_name.upper()}_PROVIDER and LLM_{tier_name.upper()}_MODEL, "
            "or leave both unset"
        )
    return provider, model


def load_llm_config(settings: Settings) -> LLMConfig:
    timeout = settings.llm_timeout_s
    shared = settings.llm_failover

    def chain(
        provider: str | None,
        model: str | None,
        effort: str | None,
        override: str | None,
        *,
        tier_name: str,
        table: dict[str, str],
    ) -> TierChain:
        resolved_provider, resolved_model = _tier_provider_model(
            provider, model, settings, tier_name=tier_name, table=table
        )
        primary = LLMTierConfig(
            provider=canonical_provider(resolved_provider),
            model=resolved_model,
            effort=effort or None,
            timeout_s=timeout,
        )
        failover = [
            c
            for c in parse_failover(override if override is not None else shared, timeout_s=timeout)
            if c.label != primary.label
        ]
        return TierChain(primary=primary, failover=failover)

    return LLMConfig(
        deep=chain(
            settings.llm_deep_provider,
            settings.llm_deep_model,
            settings.llm_deep_effort,
            settings.llm_deep_failover,
            tier_name="deep",
            table=RECOMMENDED_DEEP,
        ),
        quick=chain(
            settings.llm_quick_provider,
            settings.llm_quick_model,
            settings.llm_quick_effort,
            settings.llm_quick_failover,
            tier_name="quick",
            table=RECOMMENDED_QUICK,
        ),
    )
