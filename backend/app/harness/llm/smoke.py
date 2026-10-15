"""Manual smoke test against a real provider.

    uv run python -m app.harness.llm.smoke "สวัสดี"
    uv run python -m app.harness.llm.smoke "hello" --tier deep
    uv run python -m app.harness.llm.smoke "hello" --provider ollama --model llama3.1
    uv run python -m app.harness.llm.smoke "hello" --provider claude_code --model claude-haiku-5-5
    uv run python -m app.harness.llm.smoke --status

Prints the answer, the provider/model that answered, token counts and cost.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.domain.ports import LLMMessage, Tier
from app.harness.llm.adapter import HarnessLLM
from app.harness.llm.config import (
    PROVIDERS,
    LLMConfig,
    LLMTierConfig,
    TierChain,
    load_llm_config,
    provider_status,
)
from app.settings import get_settings


async def _main(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.status:
        for pid, info in PROVIDERS.items():
            configured, reason = provider_status(pid, settings)
            mark = "OK " if configured else "-- "
            line = f"{mark}{pid:<12} {info.label:<38} {info.env_hint}"
            if not configured and info.kind == "claude_code" and reason:  # why the CLI path is off
                line += f"    ({reason})"
            print(line)
        return 0

    config = load_llm_config(settings)
    tier: Tier = args.tier
    if args.provider:
        override = TierChain(
            primary=LLMTierConfig(
                provider=args.provider, model=args.model or "", timeout_s=settings.llm_timeout_s
            )
        )
        config = LLMConfig(deep=override, quick=override)
    llm = HarnessLLM.from_config(config, settings)

    async def on_warning(code: str, message: str) -> None:
        print(f"[warning:{code}] {message}", file=sys.stderr)

    llm = llm.bind(on_warning=on_warning)

    resp = await llm.complete(
        [
            LLMMessage(role="system", content="You are a concise assistant."),
            LLMMessage(role="user", content=args.prompt),
        ],
        tier=tier,
        purpose="agent",
    )
    print(resp.text)
    print(
        f"\n--- {resp.provider}:{resp.model} | in={resp.usage.input_tokens} out={resp.usage.output_tokens} "
        f"cache_read={resp.usage.cache_read_tokens} | cost=${resp.usage.cost_usd:.6f} | {resp.duration_ms} ms"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM smoke test")
    parser.add_argument("prompt", nargs="?", default="สวัสดี")
    parser.add_argument("--tier", choices=["deep", "quick"], default="quick")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--status", action="store_true", help="show which providers are configured")
    args = parser.parse_args()
    if args.provider and not args.model:
        parser.error("--provider needs --model")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
