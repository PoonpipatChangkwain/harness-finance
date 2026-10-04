"""App-wide configuration, read from environment / .env.

Every secret-shaped value is `SecretStr | None` so it can never be
accidentally logged, serialized into an API response, or written into a
provenance record (F3) as plain text.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "dev"
    data_dir: Path = Path("./.data")
    frontend_origin: str = "http://localhost:3000"

    # LLM (Phase 04). Provider ids: anthropic, google (AI Studio / Gemini),
    # openai (ChatGPT), xai (Grok), thaillm, ollama (local), ollama_cloud, deepseek, groq,
    # openrouter.
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GOOGLE_API_KEY", "GEMINI_API_KEY")
    )
    deepseek_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    xai_api_key: SecretStr | None = None
    thaillm_api_key: SecretStr | None = None
    thaillm_base_url: str | None = None
    ollama_base_url: str | None = None
    ollama_api_key: SecretStr | None = None  # Ollama Cloud (https://ollama.com/v1)

    # Leave provider/model unset to auto-pick the first configured provider
    # (see app.harness.llm.config.RECOMMENDED_DEEP / RECOMMENDED_QUICK).
    llm_deep_provider: str | None = None
    llm_deep_model: str | None = None
    llm_deep_effort: str | None = None
    llm_quick_provider: str | None = None
    llm_quick_model: str | None = None
    llm_quick_effort: str | None = None
    # "provider:model,provider:model" — applied to both tiers unless the
    # tier-specific override is set.
    llm_failover: str = ""
    llm_deep_failover: str | None = None
    llm_quick_failover: str | None = None
    llm_timeout_s: float = 120.0
    # "model=in/out[/cache_read]" USD per 1M tokens, comma separated; fills in
    # pricing for models the built-in catalog does not know (cost_usd is 0 otherwise).
    llm_pricing: str = ""

    # Run guards (Phase 05, D5)
    run_max_iterations: int = 10
    run_max_tool_calls_total: int = 40
    run_tool_timeout_s: float = 30.0
    run_wall_clock_s: float = 600.0
    run_max_cost_usd: float = 1.0
    run_max_output_tokens_total: int = 200_000
    run_max_calls_per_tool: int = 3
    run_similarity_threshold: float = 0.7
    # (P07) Stop → a graceful workflow gets this long to write its answer before a hard cancel.
    run_cancel_grace_s: float = 60.0

    # Policy gate (P13 Settings). None = the workflow's built-in default
    # (trading: 20% position / 2% risk per trade; advisor: per risk profile).
    policy_max_position_weight: float | None = None
    policy_max_sector_weight: float | None = None
    policy_max_risk_per_trade: float | None = None
    policy_min_confidence: float | None = None
    policy_block_on_low_confidence: bool = True  # trading: below min_confidence = no trade (False = warn)
    policy_min_risk_reward: float | None = None  # trading: R:R at the first target must exceed it (0 = off)
    policy_min_counter_trend_risk_reward: float | None = None  # trading: stricter R:R vs the daily trend
    policy_require_stop_loss: bool = True
    policy_max_turnover: float | None = None

    # Portfolio (Phase 12): weekly report every Friday 03:00 Asia/Bangkok (+ catch-up at startup)
    portfolio_weekly_enabled: bool = True

    # Graph engine (Phase 09, T1-T3)
    graph_recursion_limit: int = 100
    graph_keep_checkpoints_on_success: bool = True

    # Context management (Phase 06, D1). None = default in app.harness.context.config.
    context_mc_count_trigger: int | None = None
    context_mc_keep_recent: int | None = None
    context_mc_token_trigger: int | None = None
    context_max_output_tokens_for_summary: int | None = None
    context_autocompact_buffer_tokens: int | None = None
    context_keep_tool_uses: int | None = None
    context_max_compaction_failures: int | None = None
    context_min_tool_results: int | None = None
    context_memory_flush: bool | None = None
    context_check_pairing: bool | None = None

    # Data providers
    sec_edgar_user_agent: str | None = None
    fred_api_key: SecretStr | None = None
    alpha_vantage_api_key: SecretStr | None = None
    finnhub_api_key: SecretStr | None = None
    fmp_api_key: SecretStr | None = None
    financial_datasets_api_key: SecretStr | None = None
    x_bearer_token: SecretStr | None = None
    adanos_api_key: SecretStr | None = None
    exasearch_api_key: SecretStr | None = None
    perplexity_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    langsearch_api_key: SecretStr | None = None
    reddit_client_id: SecretStr | None = None
    reddit_client_secret: SecretStr | None = None
    sec_api_key: SecretStr | None = None
    typesafe_api_key: SecretStr | None = None

    http_user_agent: str = "FinanceHarness/0.1 (+https://github.com/)"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path.as_posix()}"

    @property
    def scratchpad_dir(self) -> Path:
        return self.data_dir / "scratchpad"

    @property
    def checkpoints_dir(self) -> Path:
        return self.data_dir / "checkpoints"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.scratchpad_dir.mkdir(parents=True, exist_ok=True)
    settings.checkpoints_dir.mkdir(parents=True, exist_ok=True)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    return settings
