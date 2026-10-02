"""Crypto headlines from public RSS feeds (CoinDesk, Cointelegraph) — no key.

yfinance has no news for BTC-USD style symbols, so crypto analysts got an empty list and kept
retrying. This provider only answers NEWS for crypto symbols; items that mention the coin come
first, followed by general crypto-market headlines. A fetch failure of one feed is a note, not
an error; if every feed fails the result is ``unavailable`` (never an empty "ok").
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from app.domain.data.base import Capability, DataRequest, DataResult
from app.domain.data.http import make_client
from app.domain.data.schemas import NewsItem
from app.domain.models.provenance import new_record
from app.domain.models.symbol import AssetType, Symbol, crypto_base

FEEDS: dict[str, str] = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss",
    "cointelegraph": "https://cointelegraph.com/rss",
}

_NAMES: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "ether", "eth"),
    "SOL": ("solana", "sol"),
    "XRP": ("xrp", "ripple"),
    "ADA": ("cardano", "ada"),
    "DOGE": ("dogecoin", "doge"),
    "LTC": ("litecoin", "ltc"),
    "BCH": ("bitcoin cash", "bch"),
    "DOT": ("polkadot",),
    "AVAX": ("avalanche", "avax"),
    "LINK": ("chainlink",),
}
_TAG_RE = re.compile(r"<[^>]+>")


def _terms(symbol: Symbol) -> tuple[str, ...]:
    base = crypto_base(symbol.canonical) or symbol.canonical.split("-")[0]
    return _NAMES.get(base, (base.lower(),))


def _mentions(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(re.search(rf"\b{re.escape(t)}\b", lowered) for t in terms)


def _parse(xml_text: str, source: str) -> list[NewsItem]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    items: list[NewsItem] = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not title or not link:
            continue
        summary = _TAG_RE.sub("", node.findtext("description") or "").strip() or None
        published: datetime | None = None
        raw_date = node.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                published = None
        items.append(
            NewsItem(
                id=(node.findtext("guid") or link).strip(),
                title=title,
                summary=summary,
                url=link,
                source=source,
                published_at=published,
            )
        )
    return items


class CryptoNewsProvider:
    id = "crypto_news"
    capabilities = frozenset({Capability.NEWS})

    def is_configured(self) -> bool:
        return True

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap == Capability.NEWS and symbol is not None and symbol.asset_type == AssetType.CRYPTO

    async def _feed(self, name: str, url: str) -> tuple[list[NewsItem], str | None]:
        try:
            async with make_client() as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as exc:
            return [], f"{name} feed failed: {exc}"
        return _parse(resp.text, name), None

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        assert req.symbol is not None
        limit = int(req.params.get("limit", 15))
        terms = _terms(req.symbol)
        fetched = await asyncio.gather(*(self._feed(n, u) for n, u in FEEDS.items()))
        notes = [note for _, note in fetched if note]
        items = [i for feed_items, _ in fetched for i in feed_items]
        if not items:
            return DataResult(
                provider=self.id, capability=Capability.NEWS, data=[], status="unavailable", notes=notes
            )

        def newest(i: NewsItem) -> float:
            return i.published_at.timestamp() if i.published_at else 0.0

        items.sort(key=newest, reverse=True)
        about = [i for i in items if _mentions(f"{i.title} {i.summary or ''}", terms)]
        rest = [i for i in items if i not in about]
        chosen = (about + rest)[:limit]
        if not about:
            notes.append(f"no headline names {req.symbol.display}; showing general crypto-market news")
        for item in chosen:
            item.tickers = [req.symbol.canonical] if item in about else []
        record = new_record(
            "data",
            {"count": len(chosen), "about_symbol": min(len(about), len(chosen))},
            source=self.id,
            tool="rss",
            params={"symbol": req.symbol.canonical},
        )
        return DataResult(
            provider=self.id,
            capability=Capability.NEWS,
            data=chosen,
            records=[record],
            status="ok",
            notes=notes,
        )
