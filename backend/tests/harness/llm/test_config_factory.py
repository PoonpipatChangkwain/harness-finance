from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.harness.llm.config import (
    LLMTierConfig,
    canonical_provider,
    is_provider_configured,
    load_llm_config,
    parse_failover,
    resolve_endpoint,
)
from app.harness.llm.errors import LLMNotConfigured
from app.harness.llm.factory import create_client
from app.settings import Settings


def _settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "anthropic_api_key": None,
        "openai_api_key": None,
        "google_api_key": None,
        "xai_api_key": None,
        "thaillm_api_key": None,
        "thaillm_base_url": None,
        "ollama_base_url": None,
        "ollama_api_key": None,
        "deepseek_api_key": None,
        "openrouter_api_key": None,
        "groq_api_key": None,
        "llm_failover": "",
    }
    base.update(kwargs)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "provider",
    [
        "anthropic",
        "google",
        "openai",
        "xai",
        "thaillm",
        "ollama",
        "ollama_cloud",
        "deepseek",
        "groq",
        "openrouter",
        "claude_code",  # the autouse fixture hides this machine's Claude Code CLI
    ],
)
def test_factory_raises_not_configured_without_key(provider: str) -> None:
    with pytest.raises(LLMNotConfigured):
        create_client(LLMTierConfig(provider=provider, model="m"), _settings())


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("gemini", "google"),
        ("AI-Studio", "google"),
        ("chatgpt", "openai"),
        ("grok", "xai"),
        ("claude", "anthropic"),
        ("ThaiLLM", "thaillm"),
        ("ollama", "ollama"),
        ("claude_sub", "claude_code"),
        ("claude-subscription", "claude_code"),
        ("claude_code_cli", "claude_code"),
    ],
)
def test_provider_aliases(alias: str, canonical: str) -> None:
    assert canonical_provider(alias) == canonical


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_provider("nope")


def test_thaillm_default_endpoint_and_override() -> None:
    assert not is_provider_configured("thaillm", _settings())
    default = _settings(thaillm_api_key=SecretStr("k"))
    assert is_provider_configured("thaillm", default)
    assert resolve_endpoint("thaillm", default).base_url == "https://thaillm.or.th/api/v1"
    s = _settings(thaillm_api_key=SecretStr("k"), thaillm_base_url="https://thaillm.example/v1/")
    endpoint = resolve_endpoint("thaillm", s)
    assert endpoint.base_url == "https://thaillm.example/v1"


def test_groq_endpoint_and_auto_pick() -> None:
    s = _settings(groq_api_key=SecretStr("k"), thaillm_api_key=SecretStr("t"))
    endpoint = resolve_endpoint("groq", s)
    assert endpoint.base_url == "https://api.groq.com/openai/v1"
    client = create_client(LLMTierConfig(provider="groq", model="openai/gpt-oss-120b"), s)
    assert type(client.chat_model).__name__ == "ChatOpenAI"
    config = load_llm_config(s)  # groq is ahead of the free thaillm in auto-pick
    assert (config.quick.primary.provider, config.quick.primary.model) == ("groq", "openai/gpt-oss-120b")


def test_ollama_is_keyless_and_gets_v1_suffix() -> None:
    endpoint = resolve_endpoint("ollama", _settings(ollama_base_url="http://localhost:11434"))
    assert endpoint.api_key is None
    assert endpoint.base_url == "http://localhost:11434/v1"


def test_ollama_cloud_uses_key_and_hosted_endpoint() -> None:
    assert canonical_provider("ollama-cloud") == "ollama_cloud"
    s = _settings(ollama_api_key=SecretStr("o"))
    assert not is_provider_configured("ollama", s)  # local still needs OLLAMA_BASE_URL
    endpoint = resolve_endpoint("ollama_cloud", s)
    assert (endpoint.api_key, endpoint.base_url) == ("o", "https://ollama.com/v1")
    client = create_client(LLMTierConfig(provider="ollama_cloud", model="gpt-oss:120b"), s)
    assert type(client.chat_model).__name__ == "ChatOpenAI"
    assert parse_failover("ollama_cloud:gemma4:31b")[0].model == "gemma4:31b"


def test_each_requested_provider_builds_a_client() -> None:
    s = _settings(
        anthropic_api_key=SecretStr("a"),
        google_api_key=SecretStr("g"),
        openai_api_key=SecretStr("o"),
        xai_api_key=SecretStr("x"),
        thaillm_api_key=SecretStr("t"),
        thaillm_base_url="https://t.example/v1",
        ollama_base_url="http://localhost:11434",
    )
    kinds = {}
    for provider in ("anthropic", "google", "openai", "xai", "thaillm", "ollama"):
        client = create_client(LLMTierConfig(provider=provider, model="m"), s)
        kinds[provider] = type(client.chat_model).__name__
    assert kinds == {
        "anthropic": "ChatAnthropic",
        "google": "ChatGoogleGenerativeAI",
        "openai": "ChatOpenAI",
        "xai": "ChatOpenAI",
        "thaillm": "ChatOpenAI",
        "ollama": "ChatOpenAI",
    }


def test_claude5_drops_temperature() -> None:
    s = _settings(anthropic_api_key=SecretStr("a"))
    client = create_client(LLMTierConfig(provider="anthropic", model="claude-sonnet-5", temperature=0.3), s)
    assert client.chat_model.temperature is None  # type: ignore[attr-defined]


def test_parse_failover() -> None:
    chain = parse_failover("openai:gpt-x, grok:grok-4 ,ollama:qwen3:8b")
    assert [(c.provider, c.model) for c in chain] == [
        ("openai", "gpt-x"),
        ("xai", "grok-4"),
        ("ollama", "qwen3:8b"),
    ]
    with pytest.raises(ValueError):
        parse_failover("openai")


def test_load_config_explicit_provider_and_failover() -> None:
    config = load_llm_config(
        _settings(
            anthropic_api_key=SecretStr("a"),
            llm_deep_provider="anthropic",
            llm_deep_model="claude-sonnet-5",
            llm_quick_provider="anthropic",
            llm_quick_model="claude-haiku-4-5",
            llm_failover="openai:gpt-x,anthropic:claude-sonnet-5",
        )
    )
    assert config.deep.primary.model == "claude-sonnet-5"
    assert config.quick.primary.model == "claude-haiku-4-5"
    # the primary is not repeated in its own failover chain
    assert [c.label for c in config.deep.failover] == ["openai:gpt-x"]
    assert [c.label for c in config.quick.failover] == ["openai:gpt-x", "anthropic:claude-sonnet-5"]


def test_load_config_auto_picks_first_configured_provider() -> None:
    """No LLM_*_PROVIDER/MODEL set: fall back to the first configured provider's recommended model."""
    config = load_llm_config(_settings(thaillm_api_key=SecretStr("t")))
    assert (config.quick.primary.provider, config.quick.primary.model) == (
        "thaillm",
        "Typhoon-S-ThaiLLM-8B-Instruct",
    )
    assert (config.deep.primary.provider, config.deep.primary.model) == (
        "thaillm",
        "Pathumma-ThaiLLM-qwen3-8b-think-3.0.0",
    )


def test_load_config_prefers_higher_priority_provider() -> None:
    config = load_llm_config(_settings(anthropic_api_key=SecretStr("a"), thaillm_api_key=SecretStr("t")))
    assert config.quick.primary.provider == "anthropic"
    assert config.quick.primary.model == "claude-haiku-4-5"


def test_load_config_auto_picks_when_env_vars_are_blank_strings() -> None:
    """``LLM_QUICK_PROVIDER=`` in a .env file reads as "", not None — must still auto-pick."""
    config = load_llm_config(
        _settings(thaillm_api_key=SecretStr("t"), llm_quick_provider="", llm_quick_model="")
    )
    assert config.quick.primary.provider == "thaillm"


def test_load_config_raises_when_nothing_configured() -> None:
    with pytest.raises(LLMNotConfigured):
        load_llm_config(_settings())


def test_load_config_partial_tier_override_rejected() -> None:
    with pytest.raises(ValueError):
        load_llm_config(_settings(anthropic_api_key=SecretStr("a"), llm_quick_provider="anthropic"))


def test_gemini_api_key_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "from-ai-studio")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.google_api_key is not None
    assert s.google_api_key.get_secret_value() == "from-ai-studio"


def test_model_choice_replaces_deep_only_when_quick_is_configured() -> None:
    from app.harness.llm.config import llm_config_for_choice

    quick = load_llm_config(
        _settings(
            thaillm_api_key=SecretStr("t"),
            llm_quick_provider="thaillm",
            llm_quick_model="Typhoon-S-ThaiLLM-8B-Instruct",
            llm_quick_failover="",
        )
    ).quick
    config = llm_config_for_choice("gemini", "gemini-3.8-flash", quick=quick)
    assert [c.label for c in config.deep.chain()] == ["google:gemini-3.8-flash"]  # no failover
    assert [c.label for c in config.quick.chain()] == ["thaillm:Typhoon-S-ThaiLLM-8B-Instruct"]
    alone = llm_config_for_choice("groq", "openai/gpt-oss-20b")
    assert alone.quick.primary.label == "groq:openai/gpt-oss-20b"


def test_claude_code_key_is_optional_and_auto_picked_after_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    """P16: CLI + token (or a local login) = configured; auto-pick ranks it right after anthropic."""
    from app.harness.llm import claude_code

    monkeypatch.setattr(claude_code, "find_cli", lambda settings: "/usr/bin/claude")
    monkeypatch.setattr(claude_code, "has_local_login", lambda: False)
    assert resolve_endpoint("claude_code", _settings()).api_key is None  # no raise without a token
    assert not is_provider_configured("claude_code", _settings())  # no token, no login

    s = _settings(anthropic_api_sub_key=SecretStr("oat"), groq_api_key=SecretStr("g"))
    config = load_llm_config(s)
    assert config.quick.primary.label == "claude_code:claude-haiku-5-5"
    assert config.deep.primary.label == "claude_code:claude-opus-5-5"
    both = load_llm_config(
        _settings(anthropic_api_sub_key=SecretStr("oat"), anthropic_api_key=SecretStr("a"))
    )
    assert both.quick.primary.provider == "anthropic"
    assert parse_failover("claude_sub:sonnet")[0].label == "claude_code:sonnet"
