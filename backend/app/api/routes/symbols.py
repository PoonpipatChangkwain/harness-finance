from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.domain.data.base import Capability, DataError, DataRequest
from app.domain.data.registry import get_router
from app.domain.data.schemas import SymbolMatch
from app.domain.models.symbol import Market, normalize_symbol

router = APIRouter(tags=["symbols"])


@router.get("/symbols/search")
async def search_symbols(q: str = Query(min_length=1), market: str | None = None) -> dict[str, object]:
    hint_market = Market(market) if market else None
    # the query itself, normalized offline (P11): the UI knows the asset type even when
    # no search provider answers
    normalized: dict[str, Any] | None
    try:
        normalized = normalize_symbol(q, hint_market).model_dump(mode="json")
    except ValueError:
        normalized = None
    req = DataRequest(params={"q": q, "market": hint_market.value if hint_market else None})
    try:
        result = await get_router().get(Capability.SYMBOL_SEARCH, req)
        matches: list[SymbolMatch] = result.data or []
    except DataError:
        matches = []
    return {"query": q, "normalized": normalized, "matches": [m.model_dump(mode="json") for m in matches]}
