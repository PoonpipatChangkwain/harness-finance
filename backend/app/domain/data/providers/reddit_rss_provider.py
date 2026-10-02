"""Reddit search RSS/Atom — no key, no OAuth.

Ported concept from ``../TradingAgents/tradingagents/dataflows/vendors/
reddit.py``: reddit's JSON search endpoint gets WAF-blocked without a
signed user agent, so this uses the public RSS search feed instead. A
fetch failure (including a 429) returns ``status="unavailable"`` — never
an empty "ok" list, since that would misreport a real absence of posts.
Superseded by ``reddit_api`` when Reddit API credentials are configured
(see ``router.py`` ``_SUPERSEDED_BY``).
"""

from __future__ import annotations

from datetime import datetime
from xml.etree import ElementTree

from app.domain.data.base import Capability, DataRequest, DataResult
from app.domain.data.http import make_client
from app.domain.data.schemas import SocialPost
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol

_SEARCH_URL = "https://www.reddit.com/search.rss"
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _parse_entries(xml_text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return entries
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title = entry.findtext(f"{_ATOM_NS}title") or ""
        link_el = entry.find(f"{_ATOM_NS}link")
        link = link_el.get("href", "") if link_el is not None else ""
        author = entry.findtext(f"{_ATOM_NS}author/{_ATOM_NS}name") or ""
        updated = entry.findtext(f"{_ATOM_NS}updated") or ""
        entry_id = entry.findtext(f"{_ATOM_NS}id") or link
        entries.append({"title": title, "link": link, "author": author, "updated": updated, "id": entry_id})
    return entries


class RedditRssProvider:
    id = "reddit_rss"
    capabilities = frozenset({Capability.SOCIAL})

    def is_configured(self) -> bool:
        return True

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities and symbol is not None

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        query = req.symbol.canonical.split(".")[0] if req.symbol else str(req.params.get("q", ""))
        params = {"q": f"${query} OR {query}", "sort": "new", "limit": str(req.params.get("limit", 25))}

        try:
            async with make_client() as client:
                resp = await client.get(_SEARCH_URL, params=params)
                if resp.status_code == 429:
                    return DataResult(
                        provider=self.id,
                        capability=Capability.SOCIAL,
                        data=[],
                        status="unavailable",
                        notes=["reddit_rss rate limited"],
                    )
                resp.raise_for_status()
        except Exception as exc:
            return DataResult(
                provider=self.id,
                capability=Capability.SOCIAL,
                data=[],
                status="unavailable",
                notes=[f"reddit_rss fetch failed: {exc}"],
            )

        entries = _parse_entries(resp.text)
        posts = []
        for e in entries:
            created_at = None
            if e["updated"]:
                try:
                    created_at = datetime.fromisoformat(e["updated"].replace("Z", "+00:00"))
                except ValueError:
                    created_at = None
            posts.append(
                SocialPost(
                    platform="reddit",
                    id=e["id"],
                    text=e["title"],
                    author=e["author"].removeprefix("/u/"),
                    created_at=created_at,
                    url=e["link"],
                )
            )
        record = new_record(
            "data", {"count": len(posts)}, source=self.id, tool="search_rss", params={"q": query}
        )
        return DataResult(
            provider=self.id, capability=Capability.SOCIAL, data=posts, records=[record], status="ok"
        )
