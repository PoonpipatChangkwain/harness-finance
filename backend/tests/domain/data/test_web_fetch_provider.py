from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.data.base import BadRequest, Capability, DataRequest
from app.domain.data.providers.web_fetch_provider import WebFetchProvider

_HTML = """
<html><head><title>Example Article</title></head>
<body><article><h1>Example Article</h1><p>This is the body text of the article.</p></article></body></html>
"""


@pytest.mark.asyncio
@respx.mock
async def test_web_fetch_extracts_markdown() -> None:
    respx.get("https://example.com/article").mock(return_value=httpx.Response(200, text=_HTML))
    provider = WebFetchProvider()

    result = await provider.fetch(
        Capability.WEB_FETCH, DataRequest(params={"url": "https://example.com/article"})
    )

    assert result.status == "ok"
    assert "body text of the article" in result.data.markdown
    assert result.data.truncated is False


@pytest.mark.asyncio
async def test_web_fetch_rejects_non_http_scheme() -> None:
    provider = WebFetchProvider()
    with pytest.raises(BadRequest):
        await provider.fetch(Capability.WEB_FETCH, DataRequest(params={"url": "file:///etc/passwd"}))


@pytest.mark.asyncio
async def test_web_fetch_rejects_loopback_host() -> None:
    provider = WebFetchProvider()
    with pytest.raises(BadRequest):
        await provider.fetch(Capability.WEB_FETCH, DataRequest(params={"url": "http://127.0.0.1:8000/admin"}))


@pytest.mark.asyncio
async def test_web_fetch_rejects_private_ip_host() -> None:
    provider = WebFetchProvider()
    with pytest.raises(BadRequest):
        await provider.fetch(Capability.WEB_FETCH, DataRequest(params={"url": "http://10.0.0.5/secret"}))


@pytest.mark.asyncio
async def test_web_fetch_requires_url() -> None:
    provider = WebFetchProvider()
    with pytest.raises(BadRequest):
        await provider.fetch(Capability.WEB_FETCH, DataRequest())
