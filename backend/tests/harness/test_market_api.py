"""``/api/market/{symbol}/ohlcv`` and ``/indicators`` (phase-11 §11.2 PriceChart)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.api.main import app
from app.domain.compute import indicators as ind
from app.domain.data.base import Capability, DataRequest, DataResult
from app.domain.models.market import OHLCVBar, OHLCVFrame
from app.domain.models.provenance import new_record
from app.harness.runtime.services import build_services
from app.settings import Settings

pytestmark = pytest.mark.usefixtures("db")

START = datetime(2024, 1, 1, tzinfo=UTC)
CLOSES = [100 + (i % 17) - (i % 5) * 0.5 + i * 0.05 for i in range(600)]


class _Router:
    def __init__(self) -> None:
        self.requests: list[DataRequest] = []

    async def get(self, cap: Capability, req: DataRequest) -> DataResult:
        self.requests.append(req)
        interval = req.params.get("interval", "1d")
        step = timedelta(minutes=5) if interval == "5m" else timedelta(days=1)
        bars = [
            OHLCVBar(ts=START + step * i, open=Decimal(str(c)), high=Decimal(str(c + 1)),
                     low=Decimal(str(c - 1)), close=Decimal(str(c)), volume=1000 + i)
            for i, c in enumerate(CLOSES)
        ]  # fmt: skip
        assert req.symbol is not None
        frame = OHLCVFrame(symbol=req.symbol.canonical, interval=interval, bars=bars)
        record = new_record("data", CLOSES[-1], source="fake", tool="prices")
        return DataResult(provider="fake", capability=cap, data=frame, records=[record], status="ok")


@pytest.fixture
def router(monkeypatch: pytest.MonkeyPatch, settings: Settings, install_services: Any) -> _Router:
    install_services(build_services(Settings(data_dir=settings.data_dir)))
    fake = _Router()
    monkeypatch.setattr("app.api.routes.market.get_router", lambda: fake)
    return fake


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=10)


async def test_indicators_are_backend_computed_and_trimmed(router: _Router) -> None:
    async with _client() as client:
        resp = await client.get(
            "/api/market/PTT/indicators", params={"set": "sma20,sma200,bb20,rsi14,macd", "range": "6mo"}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert router.requests[0].params["range"] == "2y"  # warm-up history fetched
    n = len(body["ts"])
    assert 150 < n < 200  # ≈ 183 daily bars
    assert all(len(v) == n for v in body["series"].values())
    assert set(body["series"]) >= {"sma20", "sma200", "bb_upper", "bb_lower", "rsi14", "macd", "macd_hist"}
    # same numbers as the compute function, and SMA200 is defined over the whole range thanks to warm-up
    expected = ind.sma(CLOSES, 200).value[-n:]
    assert body["series"]["sma200"] == pytest.approx(expected)
    assert body["series"]["sma200"][0] is not None
    # one compute provenance record per set, with the price record as input
    async with _client() as client:
        for name, pid in body["provenance"].items():
            rec = (await client.get(f"/api/provenance/{pid}")).json()["record"]
            assert rec["kind"] == "compute" and rec["inputs"] == [body["price_provenance_id"]], name


async def test_as_of_cuts_bars_and_bad_input_is_400(router: _Router) -> None:
    cut = date(2024, 6, 30)
    async with _client() as client:
        ohlcv = (await client.get("/api/market/PTT/ohlcv", params={"as_of": cut.isoformat()})).json()
        assert ohlcv["bars"][-1]["ts"].startswith("2024-06-30")
        assert ohlcv["currency"] == "THB" and ohlcv["provenance_id"]
        assert (await client.get("/api/market/PTT/indicators", params={"set": "evil"})).status_code == 400
        assert (await client.get("/api/market/PTT/indicators", params={"range": "7d"})).status_code == 400
        assert (await client.get("/api/market/PTT/ohlcv", params={"interval": "2m"})).status_code == 400
        assert (await client.get("/api/market/PTT/indicators", params={"interval": "2m"})).status_code == 400


async def test_intraday_indicators_use_intraday_warmup_and_trim_by_timestamp(router: _Router) -> None:
    async with _client() as client:
        resp = await client.get(
            "/api/market/GBPJPY=X/indicators", params={"set": "sma20", "interval": "5m", "range": "1d"}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    req = router.requests[0]
    assert req.params == {"interval": "5m", "range": "1mo"}  # not the daily "2y" warm-up
    # 600 five-minute bars span ~2 days; range 1d keeps the last 24h = 288 bars, not the whole last session
    assert len(body["ts"]) == 288
    assert body["series"]["sma20"][0] is not None
    # candles cover exactly the indicator window, from the same (cached) fetch
    async with _client() as client:
        ohlcv = (
            await client.get("/api/market/GBPJPY=X/ohlcv", params={"interval": "5m", "range": "1d"})
        ).json()
    assert router.requests[1].params == {"interval": "5m", "range": "1mo"}
    parse = datetime.fromisoformat
    assert [parse(b["ts"]) for b in ohlcv["bars"]] == [parse(t) for t in body["ts"]]
