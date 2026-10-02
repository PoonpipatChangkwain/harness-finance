"""StockTwits public symbol stream — no key, no auth.

Ported from ``../TradingAgents/tradingagents/dataflows/vendors/stocktwits.py``:
a per-symbol message stream with a user-labeled Bullish/Bearish sentiment
tag. Crypto symbols map to StockTwits' ``<BASE>.X`` convention. A fetch
failure is ``unavailable`` (this vendor cannot answer right now), never
"ok with an empty list" (which would misreport a real absence of chatter).
"""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.domain.data.base import Capability, DataRequest, DataResult, DataStatus, Transient
from app.domain.data.http import make_client, request_with_retry
from app.domain.data.schemas import SocialPost
from app.domain.data.window import in_window
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol, crypto_base

_API = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"


def _stocktwits_symbol(ticker: str) -> str:
    base = crypto_base(ticker)
    return f"{base}.X" if base else ticker.strip().upper()


def _created_at(message: dict[str, object]) -> datetime | None:
    raw = message.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


class StocktwitsProvider:
    id = "stocktwits"
    capabilities = frozenset({Capability.SOCIAL})

    def is_configured(self) -> bool:
        return True

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities and symbol is not None

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Transient("SOCIAL requires a symbol")
        symbol = _stocktwits_symbol(req.symbol.canonical.removesuffix(".BK").removesuffix("=X"))

        try:
            async with make_client() as client:
                resp = await request_with_retry(client, "GET", _API.format(ticker=symbol))
        except Exception as exc:
            return DataResult(
                provider=self.id,
                capability=Capability.SOCIAL,
                data=[],
                status="unavailable",
                notes=[f"stocktwits fetch failed: {exc}"],
            )

        body = resp.json()
        messages = body.get("messages", [])

        if req.as_of is not None:
            end = datetime.combine(req.as_of, datetime.min.time(), tzinfo=UTC)
            lookback = timedelta(days=int(req.params.get("lookback_days", 7)))
            start = end - lookback
            filtered = [m for m in messages if in_window(_created_at(m), start, end)]
        else:
            filtered = messages

        posts = []
        for m in filtered:
            entities = m.get("entities") or {}
            sentiment = (entities.get("sentiment") or {}).get("basic")
            stance = sentiment.lower() if isinstance(sentiment, str) else None
            stance_label: Literal["bullish", "bearish"] | None = (
                "bullish" if stance == "bullish" else "bearish" if stance == "bearish" else None
            )
            posts.append(
                SocialPost(
                    platform="stocktwits",
                    id=str(m.get("id")),
                    text=html.unescape(m.get("body") or ""),
                    author=(m.get("user") or {}).get("username"),
                    created_at=_created_at(m),
                    url=f"https://stocktwits.com/symbol/{symbol}/message/{m.get('id')}",
                    stance_label=stance_label,
                )
            )
        record = new_record(
            "data", {"count": len(posts)}, source=self.id, tool="social_stream", as_of=req.as_of
        )
        status: DataStatus = "ok" if posts or not req.as_of else "unavailable"
        return DataResult(
            provider=self.id, capability=Capability.SOCIAL, data=posts, records=[record], status=status
        )
