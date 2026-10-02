from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.tools.common import SymbolArgs, to_symbol
from app.domain.tools.market import PricesArgs, SectorArgs


@pytest.mark.parametrize("market", ["US", "TH", "GLOBAL", "CRYPTO", "FX", "crypto", "fx"])
def test_market_hint_accepts_known_values(market: str) -> None:
    assert SymbolArgs(symbol="BTC-USD", market=market).market == market.upper()  # type: ignore[arg-type]


@pytest.mark.parametrize("market", ["USD", "forex", "world", ""])
def test_market_hint_rejects_unknown_values(market: str) -> None:
    with pytest.raises(ValidationError):
        SymbolArgs(symbol="GBPJPY", market=market)  # type: ignore[arg-type]


def test_non_exchange_hints_do_not_change_symbol_resolution() -> None:
    assert to_symbol("GBPJPY", "FX").canonical == "GBPJPY=X"
    assert to_symbol("BTCUSD", "CRYPTO").canonical == "BTC-USD"


def test_sector_lists_only_take_exchanges() -> None:
    with pytest.raises(ValidationError):
        SectorArgs(market="CRYPTO")  # type: ignore[arg-type]


def test_sub_five_day_range_maps_to_5d() -> None:
    assert PricesArgs(symbol="BTC-USD", range="2d").range == "5d"  # type: ignore[arg-type]
