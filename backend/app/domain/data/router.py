"""DataRouter: capability -> ordered providers, with failover or fanout (F4).

Ported pattern from ``../TradingAgents/tradingagents/dataflows/router.py``:
the agent asks for a capability, not a vendor. Failover tries providers in
order and stops at the first ``ok``; fanout calls every configured
provider concurrently and merges. A core capability that exhausts every
provider raises loudly (``AllProvidersFailed``); an optional one degrades
to ``status="unavailable"`` instead (``OPTIONAL_CAPABILITIES``).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel

from app.domain.data.base import (
    OPTIONAL_CAPABILITIES,
    BadRequest,
    Capability,
    DataError,
    DataProvider,
    DataRequest,
    DataResult,
    DataStatus,
    NoData,
    NotConfigured,
    RateLimited,
    Transient,
    Unsupported,
)

RouterMode = Literal["failover", "fanout"]

_ROUTING_YAML = Path(__file__).resolve().parent / "routing.yaml"

# A keyless provider that a paid/free-key provider replaces once configured,
# so fanout capabilities (e.g. SOCIAL) don't double-count the same source.
_SUPERSEDED_BY: dict[str, str] = {"reddit_rss": "reddit_api"}


class ProviderAttempt(BaseModel):
    provider: str
    outcome: str
    duration_ms: int
    error: str | None = None


class AllProvidersFailed(Exception):
    def __init__(self, capability: Capability, attempts: list[ProviderAttempt]) -> None:
        self.capability = capability
        self.attempts = attempts
        tried = ", ".join(f"{a.provider}={a.outcome}" for a in attempts)
        super().__init__(f"all providers failed for {capability.value}: {tried}")


def _load_routing_config() -> dict[str, dict[str, object]]:
    if not _ROUTING_YAML.exists():
        return {}
    with _ROUTING_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class DataRouter:
    def __init__(self, providers: dict[str, DataProvider] | None = None) -> None:
        self._providers: dict[str, DataProvider] = providers or {}
        self._config = _load_routing_config()
        self._order_overrides: dict[str, list[str]] = {}

    def register(self, provider: DataProvider) -> None:
        self._providers[provider.id] = provider

    def providers(self) -> dict[str, DataProvider]:
        return dict(self._providers)

    def default_order(self, cap: Capability) -> tuple[RouterMode, list[str]]:
        """Mode and provider order from ``routing.yaml`` (before any user override)."""
        entry = self._config.get(cap.value, {})
        return cast("RouterMode", entry.get("mode", "failover")), list(
            cast("list[str]", entry.get("order", []))
        )

    def allowed_providers(self, cap: Capability) -> list[str]:
        """Ids allowed in ``cap``'s order: the configured defaults + registered providers of ``cap``."""
        _, order = self.default_order(cap)
        extra = [pid for pid, p in self._providers.items() if cap in p.capabilities and pid not in order]
        return [*order, *extra]

    def set_order_overrides(self, orders: dict[str, list[str]]) -> None:
        """User-chosen order per capability (Settings page); a missing capability keeps its default."""
        self._order_overrides = {k: list(v) for k, v in orders.items()}

    def _order_for(self, cap: Capability, mode_override: RouterMode | None) -> tuple[RouterMode, list[str]]:
        mode, order = self.default_order(cap)
        override = self._order_overrides.get(cap.value)
        if override is None:
            return mode_override or mode, order
        # a saved order predates providers added since: keep them, after the user's order
        return mode_override or mode, [*override, *(p for p in order if p not in override)]

    def _candidates(self, cap: Capability, order: list[str], symbol: object) -> list[DataProvider]:
        candidates = []
        for provider_id in order:
            supersedes = _SUPERSEDED_BY.get(provider_id)
            if supersedes:
                better = self._providers.get(supersedes)
                if better is not None and better.is_configured():
                    continue
            provider = self._providers.get(provider_id)
            if provider is None:
                continue
            if cap not in provider.capabilities:
                continue
            candidates.append(provider)
        return candidates

    async def get(self, cap: Capability, req: DataRequest, *, mode: RouterMode | None = None) -> DataResult:
        router_mode, order = self._order_for(cap, mode)
        candidates = self._candidates(cap, order, req.symbol)

        if router_mode == "fanout":
            return await self._fanout(cap, req, candidates)
        return await self._failover(cap, req, candidates)

    async def _run_one(
        self, provider: DataProvider, cap: Capability, req: DataRequest
    ) -> tuple[DataResult | None, ProviderAttempt]:
        start = time.monotonic()
        if not provider.is_configured():
            return None, ProviderAttempt(
                provider=provider.id, outcome="skipped_not_configured", duration_ms=0
            )
        if not provider.supports(cap, req.symbol):
            return None, ProviderAttempt(provider=provider.id, outcome="skipped_unsupported", duration_ms=0)
        try:
            result = await provider.fetch(cap, req)
        except NotConfigured:
            return None, ProviderAttempt(
                provider=provider.id, outcome="skipped_not_configured", duration_ms=_ms(start)
            )
        except Unsupported as exc:
            return None, ProviderAttempt(
                provider=provider.id, outcome="unsupported", duration_ms=_ms(start), error=str(exc)
            )
        except NoData as exc:
            return None, ProviderAttempt(
                provider=provider.id, outcome="no_data", duration_ms=_ms(start), error=str(exc)
            )
        except RateLimited as exc:
            return None, ProviderAttempt(
                provider=provider.id, outcome="rate_limited", duration_ms=_ms(start), error=str(exc)
            )
        except Transient as exc:
            return None, ProviderAttempt(
                provider=provider.id, outcome="transient", duration_ms=_ms(start), error=str(exc)
            )
        except BadRequest as exc:
            return None, ProviderAttempt(
                provider=provider.id, outcome="bad_request", duration_ms=_ms(start), error=str(exc)
            )
        except DataError as exc:
            return None, ProviderAttempt(
                provider=provider.id, outcome="error", duration_ms=_ms(start), error=str(exc)
            )
        return result, ProviderAttempt(provider=provider.id, outcome="ok", duration_ms=_ms(start))

    async def _failover(
        self, cap: Capability, req: DataRequest, candidates: list[DataProvider]
    ) -> DataResult:
        attempts: list[ProviderAttempt] = []
        for provider in candidates:
            result, attempt = await self._run_one(provider, cap, req)
            attempts.append(attempt)
            if attempt.outcome == "bad_request":
                raise BadRequest(attempt.error or "bad request")
            if result is not None:
                return result
        if cap in OPTIONAL_CAPABILITIES:
            return DataResult(
                provider="none",
                capability=cap,
                data=None,
                status="unavailable",
                notes=[f"{a.provider}: {a.outcome}" for a in attempts],
            )
        raise AllProvidersFailed(cap, attempts)

    async def _fanout(self, cap: Capability, req: DataRequest, candidates: list[DataProvider]) -> DataResult:
        configured = [p for p in candidates if p.is_configured() and p.supports(cap, req.symbol)]
        if not configured:
            if cap in OPTIONAL_CAPABILITIES:
                return DataResult(
                    provider="none",
                    capability=cap,
                    data=None,
                    status="unavailable",
                    notes=["no provider configured"],
                )
            raise AllProvidersFailed(cap, [])

        results = await asyncio.gather(*(self._run_one(p, cap, req) for p in configured))
        oks = [(r, a) for r, a in results if r is not None]
        notes = [
            f"{a.provider}: {a.outcome}" + (f" ({a.error})" if a.error else "")
            for _, a in results
            if a.outcome != "ok"
        ]

        if not oks:
            if cap in OPTIONAL_CAPABILITIES:
                return DataResult(
                    provider="none", capability=cap, data=None, status="unavailable", notes=notes
                )
            raise AllProvidersFailed(cap, [a for _, a in results])

        merged_data: list[object] = []
        merged_records = []
        for result, _attempt in oks:
            if isinstance(result.data, list):
                merged_data.extend(result.data)
            elif result.data is not None:
                merged_data.append(result.data)
            merged_records.extend(result.records)

        status: DataStatus = "ok" if len(oks) == len(configured) else "partial"
        provider_names = "+".join(sorted(a.provider for _, a in oks))

        if cap == Capability.SOCIAL and req.symbol is not None:
            merged_data, jev_note = await self._jev_screen(req.symbol.display, merged_data)
            if jev_note:
                notes.append(jev_note)

        return DataResult(
            provider=provider_names,
            capability=cap,
            data=merged_data,
            records=merged_records,
            status=status,
            notes=notes,
        )

    async def _jev_screen(self, instrument: str, posts: list[object]) -> tuple[list[object], str | None]:
        from app.domain.data.postprocess.jev_screen import jev_screen
        from app.domain.data.schemas import SocialPost

        if not all(isinstance(p, SocialPost) for p in posts):
            return posts, None
        result = await jev_screen(instrument, list(posts))  # type: ignore[arg-type]
        if result is None:
            return posts, None
        return list(result.kept), result.note


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
