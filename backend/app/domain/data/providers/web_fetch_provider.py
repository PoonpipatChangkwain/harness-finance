"""Generic web page fetcher: httpx + readability + markdownify.

Ported concept from ``../dexter/src/tools/fetch/web-fetch.ts``: extract the
readable article body (not the whole DOM), convert to markdown, and cap
length so one page can't blow the context budget. Blocks non-http(s)
schemes and requests to loopback/private/link-local addresses (SSRF guard)
since the URL can come from LLM-followed, attacker-influenced content.
"""

from __future__ import annotations

import ipaddress
import socket
from datetime import UTC, datetime
from urllib.parse import urlparse

from markdownify import markdownify
from readability import Document

from app.domain.data.base import BadRequest, Capability, DataRequest, DataResult, Transient
from app.domain.data.http import make_client
from app.domain.data.schemas import WebPage
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol

_MAX_CHARS = 40_000
_ALLOWED_SCHEMES = {"http", "https"}


def _is_blocked_host(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise BadRequest(f"unsupported URL scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise BadRequest("URL has no hostname")
    if _is_blocked_host(parsed.hostname):
        raise BadRequest(f"refusing to fetch internal/private host: {parsed.hostname!r}")


class WebFetchProvider:
    id = "web_fetch"
    capabilities = frozenset({Capability.WEB_FETCH})

    def is_configured(self) -> bool:
        return True

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        url = str(req.params.get("url", ""))
        if not url:
            raise BadRequest("WEB_FETCH requires a url param")
        _validate_url(url)

        try:
            async with make_client(follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except BadRequest:
            raise
        except Exception as exc:
            raise Transient(f"web_fetch failed for {url}: {exc}") from exc

        # Redirects can land on an internal address; re-validate the final URL.
        final_url = str(resp.url)
        _validate_url(final_url)

        doc = Document(resp.text)
        title = doc.short_title()
        content_html = doc.summary()
        markdown = markdownify(content_html, heading_style="ATX").strip()

        truncated = len(markdown) > _MAX_CHARS
        if truncated:
            markdown = markdown[:_MAX_CHARS]

        page = WebPage(
            url=final_url, title=title, markdown=markdown, fetched_at=datetime.now(UTC), truncated=truncated
        )
        record = new_record(
            "data", {"title": title, "chars": len(markdown)}, source=self.id, tool="web_fetch", url=final_url
        )
        return DataResult(
            provider=self.id, capability=Capability.WEB_FETCH, data=page, records=[record], status="ok"
        )
