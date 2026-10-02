from __future__ import annotations

import pytest

from app.domain.models.symbol import AssetType, Market, normalize_symbol, safe_path_component


@pytest.mark.parametrize(
    ("raw", "hint", "canonical", "asset_type", "market"),
    [
        ("PTT", None, "PTT.BK", AssetType.STOCK, Market.TH),
        ("ptt.bk", None, "PTT.BK", AssetType.STOCK, Market.TH),
        ("PTTEP", None, "PTTEP.BK", AssetType.STOCK, Market.TH),
        ("AAPL", None, "AAPL", AssetType.STOCK, Market.US),
        ("aapl", None, "AAPL", AssetType.STOCK, Market.US),
        ("MSFT", Market.US, "MSFT", AssetType.STOCK, Market.US),
        ("eurusd", None, "EURUSD=X", AssetType.FOREX, Market.GLOBAL),
        ("USDJPY", None, "USDJPY=X", AssetType.FOREX, Market.GLOBAL),
        ("USDTHB", None, "USDTHB=X", AssetType.FOREX, Market.GLOBAL),
        ("XAUUSD", None, "XAUUSD", AssetType.COMMODITY, Market.GLOBAL),  # spot, not COMEX futures
        ("XAUUSD+", None, "XAUUSD", AssetType.COMMODITY, Market.GLOBAL),
        ("XAGUSD", None, "XAGUSD", AssetType.COMMODITY, Market.GLOBAL),
        ("GOLD", None, "XAUUSD", AssetType.COMMODITY, Market.GLOBAL),
        ("GC=F", None, "GC=F", AssetType.COMMODITY, Market.GLOBAL),  # futures typed explicitly
        ("SI=F", None, "SI=F", AssetType.COMMODITY, Market.GLOBAL),
        ("BTCUSD", None, "BTC-USD", AssetType.CRYPTO, Market.GLOBAL),
        ("BTCUSDT", None, "BTC-USD", AssetType.CRYPTO, Market.GLOBAL),
        ("BTC-USD", None, "BTC-USD", AssetType.CRYPTO, Market.GLOBAL),
        ("ETHUSD", None, "ETH-USD", AssetType.CRYPTO, Market.GLOBAL),
        ("^SET.BK", None, "^SET.BK", AssetType.INDEX, Market.TH),
        ("^SET", None, "^SET.BK", AssetType.INDEX, Market.TH),
        ("^GSPC", None, "^GSPC", AssetType.INDEX, Market.GLOBAL),
        ("KBANK", None, "KBANK.BK", AssetType.STOCK, Market.TH),
        ("SCB", None, "SCB.BK", AssetType.STOCK, Market.TH),
        ("TSLA", Market.US, "TSLA", AssetType.STOCK, Market.US),
        ("NVDA.M", None, "NVDA", AssetType.STOCK, Market.US),
        ("EURUSD-ECN", None, "EURUSD=X", AssetType.FOREX, Market.GLOBAL),
        ("GOOGL", None, "GOOGL", AssetType.STOCK, Market.US),
        ("SOMESTOCK", Market.TH, "SOMESTOCK.BK", AssetType.STOCK, Market.TH),
    ],
)
def test_normalize_symbol(raw, hint, canonical, asset_type, market) -> None:
    symbol = normalize_symbol(raw, hint_market=hint)
    assert symbol.canonical == canonical
    assert symbol.asset_type == asset_type
    assert symbol.market == market
    assert symbol.raw == raw


def test_normalize_symbol_empty_raises() -> None:
    with pytest.raises(ValueError):
        normalize_symbol("")


def test_normalize_symbol_currency() -> None:
    assert normalize_symbol("PTT").currency == "THB"
    assert normalize_symbol("AAPL").currency == "USD"
    assert normalize_symbol("EURUSD").currency == "USD"
    assert normalize_symbol("GBPJPY").currency == "JPY"
    assert normalize_symbol("BTCUSD").currency == "USD"


@pytest.mark.parametrize(
    "value",
    ["AAPL", "PTT.BK", "GC=F", "^GSPC", "EURUSD=X", "BTC-USD"],
)
def test_safe_path_component_ok(value: str) -> None:
    assert safe_path_component(value) == value


@pytest.mark.parametrize(
    "value",
    ["../../etc/passwd", "..", ".", "a/b", "a\\b", "", "x" * 40],
)
def test_safe_path_component_rejects(value: str) -> None:
    with pytest.raises(ValueError):
        safe_path_component(value)
