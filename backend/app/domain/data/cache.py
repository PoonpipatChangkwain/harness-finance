"""File cache for data-provider results, keyed by (provider, capability, params, as_of).

Ported concept from ``../dexter/src/tools/finance/api.ts`` (readCache/
writeCache) and ``../TradingAgents/tradingagents/dataflows/config.py``
(cache directory). A cache hit returns the *original* ``DataResult`` --
including its provenance records' original ``fetched_at`` -- so replaying
from cache never claims data was fetched more recently than it was.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from app.domain.data.base import Capability, DataResult
from app.settings import get_settings

# TTL per capability, seconds. Values not listed fall back to _DEFAULT_TTL.
_TTL_BY_CAPABILITY: dict[Capability, int] = {
    Capability.PRICES: 6 * 3600,
    Capability.QUOTE: 60,
    Capability.STATEMENTS: 7 * 24 * 3600,
    Capability.FUNDAMENTALS: 24 * 3600,
    Capability.NEWS: 15 * 60,
    Capability.GLOBAL_NEWS: 15 * 60,
    Capability.SOCIAL: 10 * 60,
    Capability.MACRO: 12 * 3600,
    Capability.PREDICTION_MARKETS: 3600,
    Capability.PROFILE: 24 * 3600,
    Capability.FILINGS: 24 * 3600,
    Capability.ANALYST: 3600,
    Capability.INSIDER: 24 * 3600,
    Capability.INSTITUTIONAL: 24 * 3600,
    Capability.WEB_SEARCH: 3600,
    Capability.WEB_FETCH: 24 * 3600,
    Capability.SYMBOL_SEARCH: 24 * 3600,
}
_DEFAULT_TTL = 3600
_INTRADAY_PRICES_TTL = 60


def _ttl_seconds(cap: Capability, params: dict[str, Any]) -> int:
    if cap is Capability.PRICES:
        interval = str(params.get("interval", "1d"))
        if interval not in ("1d", "1wk"):
            return _INTRADAY_PRICES_TTL
    return _TTL_BY_CAPABILITY.get(cap, _DEFAULT_TTL)


def _cache_key(provider: str, cap: Capability, params: dict[str, Any], as_of: str | None) -> str:
    canonical = json.dumps({"params": params, "as_of": as_of}, sort_keys=True, default=str)
    digest = hashlib.sha256(f"{provider}|{cap.value}|{canonical}".encode()).hexdigest()
    return digest


def _cache_path(key: str) -> Path:
    return get_settings().cache_dir / f"{key}.json"


def get(provider: str, cap: Capability, params: dict[str, Any], as_of: str | None) -> DataResult | None:
    path = _cache_path(_cache_key(provider, cap, params, as_of))
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload["expires_at"] < time.time():
        return None
    return DataResult.model_validate(payload["result"])


def set(
    provider: str, cap: Capability, params: dict[str, Any], as_of: str | None, result: DataResult
) -> None:
    path = _cache_path(_cache_key(provider, cap, params, as_of))
    payload = {
        "expires_at": time.time() + _ttl_seconds(cap, params),
        "result": result.model_dump(mode="json"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")
