from __future__ import annotations

import httpx
import pytest

from app.api.main import app


@pytest.mark.asyncio
async def test_config_status_lists_all_23_providers() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/config/status")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 23
    for entry in data.values():
        assert set(entry) == {"configured", "tier", "capabilities"}
        assert entry["tier"] in ("A", "B", "C")

    # Tier A providers require no key and must show as configured.
    assert data["yfinance"]["configured"] is True
    assert data["static"]["configured"] is True


@pytest.mark.asyncio
async def test_config_status_never_returns_a_key_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "super-secret-value-should-not-leak")
    from app.settings import get_settings

    get_settings.cache_clear()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/config/status")
        assert "super-secret-value-should-not-leak" not in resp.text
    finally:
        get_settings.cache_clear()
