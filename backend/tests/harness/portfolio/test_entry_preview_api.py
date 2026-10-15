"""P16 §16.2 over HTTP: ``…/positions/preview`` and ``/portfolios/parse-holdings`` (no network)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.domain.portfolio.paste import ParsedHolding, ParsedHoldings
from app.harness.llm.errors import LLMNotConfigured
from app.harness.llm.fake import FakeLLM, FakeTurn
from app.harness.storage import portfolio_repo
from app.settings import Settings
from tests.domain.portfolio.fakes import FakeMarket, install
from tests.harness.portfolio.helpers import client, portfolio_services, save_book

pytestmark = pytest.mark.usefixtures("db")

DIME_TEXT = "NVDA 549.96 USD +14.52%\nAAPL 2,000.00 USD -3.10%"
STREAMING_TEXT = (
    "หุ้น  จำนวน  ราคาเฉลี่ย  ราคาล่าสุด  %กำไร\nPTT 1,000 32.50 35.00 +7.69%\nKBANK 100 150.00 145.50 -3.00%"
)


@pytest.fixture(autouse=True)
def market(monkeypatch: pytest.MonkeyPatch) -> FakeMarket:
    return install(monkeypatch, FakeMarket({**FakeMarket().quotes, "NVDA": ("180", "175")}))


async def test_preview_resolves_modes_without_saving(settings: Settings, install_services: Any) -> None:
    install_services(portfolio_services(settings))
    body = {
        "positions": [
            {"symbol": "PTT", "mode": "qty_pnl_amount", "quantity": "1000", "pnl_amount": "4500"},
            {"symbol": "AAPL", "value": "2000", "pnl_pct": "0.25"},
            {"symbol": "KBANK", "mode": "qty_cost", "quantity": "100", "pnl_pct": "0.1"},
        ]
    }
    async with client() as c:
        resp = await c.post("/api/portfolio/positions/preview", json=body)  # pf_default before any save
        assert resp.status_code == 200, resp.text
        out = resp.json()
        ptt, aapl, kbank = out["rows"]
        assert ptt["status"] == "ok" and ptt["mode"] == "qty_pnl_amount"
        assert Decimal(ptt["avg_cost"]) == 30 and Decimal(ptt["market_value"]) == 34500
        assert Decimal(ptt["unrealized_pnl"]) == 4500 and Decimal(ptt["unrealized_pnl_pct"]) == Decimal(
            "0.15"
        )
        assert ptt["symbol"]["canonical"] == "PTT.BK" and ptt["currency"] == "THB"
        assert ptt["price"] == "34.5" and ptt["price_as_of"] == "2026-09-25"
        assert aapl["mode"] == "value_pnl_pct" and Decimal(aapl["quantity"]) == 10
        assert kbank["status"] == "error" and kbank["errors"][0]["field"] == "pnl_pct"
        assert out["errors"] == []
        # every number's provenance id resolves (stored), and the valuation traces back to the price
        for name, pid in ptt["provenance"].items():
            assert (await c.get(f"/api/provenance/{pid}")).status_code == 200, name
        lineage = (await c.get(f"/api/provenance/{ptt['provenance_id']}?lineage=1")).json()
        assert ptt["provenance"]["price"] in str(lineage)
        # nothing was saved: the default book is still "not provided"
        assert (await c.get("/api/portfolio")).status_code == 404
        assert await portfolio_repo.get_portfolio(portfolio_repo.DEFAULT_PORTFOLIO_ID) is None


async def test_preview_on_a_saved_book_and_unknown_id(settings: Settings, install_services: Any) -> None:
    install_services(portfolio_services(settings))
    await save_book("pf_x", name="X")
    async with client() as c:
        ok = await c.post(
            "/api/portfolios/pf_x/positions/preview",
            json={"positions": [{"symbol": "AAPL", "quantity": "1"}]},
        )
        assert ok.status_code == 200 and ok.json()["rows"][0]["status"] == "ok"
        missing = await c.post("/api/portfolios/pf_nope/positions/preview", json={"positions": []})
        assert missing.status_code == 404
        book = await c.post(
            "/api/portfolios/pf_x/positions/preview",
            json={"positions": [{"symbol": "PTT", "quantity": "1"}, {"symbol": "AAPL", "weight": "0.5"}]},
        )
        assert book.json()["errors"][0]["index"] is None  # all-% or all-quantity, per book
    saved = await portfolio_repo.get_portfolio("pf_x")
    assert saved is not None and len(saved.positions) == 2  # untouched


async def test_preview_warns_when_price_is_missing(
    settings: Settings, install_services: Any, market: FakeMarket
) -> None:
    install_services(portfolio_services(settings))
    market.missing = {"MSFT"}
    async with client() as c:
        rows = (
            await c.post(
                "/api/portfolio/positions/preview",
                json={
                    "positions": [
                        {"symbol": "MSFT", "quantity": "2", "cost_total": "700"},
                        {"symbol": "MSFT.X", "value": "100"},
                    ]
                },
            )
        ).json()["rows"]
    msft = rows[0]
    assert msft["status"] == "ok" and msft["price"] is None and msft["market_value"] is None
    assert Decimal(msft["avg_cost"]) == 350 and Decimal(msft["cost_basis"]) == 700
    assert msft["errors"][0]["severity"] == "warning" and msft["errors"][0]["field"] == "price"


async def test_put_accepts_the_new_modes_and_422_names_the_field(
    settings: Settings, install_services: Any
) -> None:
    install_services(portfolio_services(settings))
    async with client() as c:
        bad = await c.put(
            "/api/portfolio",
            json={"positions": [{"symbol": "PTT", "mode": "cost_total", "quantity": "10", "value": "5"}]},
        )
        assert bad.status_code == 422 and bad.json()["errors"][0]["field"] == "value"
        good = await c.put(
            "/api/portfolio",
            json={
                "cash": "0",
                "positions": [
                    {"symbol": "PTT", "quantity": "1000", "pnl_pct": "0.15"},
                    {"symbol": "KBANK", "quantity": "100", "cost_total": "14000"},
                ],
            },
        )
        assert good.status_code == 200, good.text
        positions = {p["symbol"]["canonical"]: p for p in good.json()["portfolio"]["positions"]}
        assert Decimal(positions["PTT.BK"]["avg_cost"]) == pytest.approx(Decimal(30))
        assert Decimal(positions["KBANK.BK"]["avg_cost"]) == 140
        a = (await c.get("/api/portfolio/analytics")).json()
        assert a["unrealized_pnl"]["value"] == pytest.approx(4500 + 1000)  # matches what preview showed


async def test_csv_import_preview_with_new_columns(settings: Settings, install_services: Any) -> None:
    install_services(portfolio_services(settings))
    csv_text = (
        "symbol,quantity,value,pnl_amount,cost_total,mode\n"
        "PTT,1000,,4500,,\n"
        "AAPL,,2000,400,,value_pnl_amount\n"
    )
    async with client() as c:
        resp = await c.post("/api/portfolio/import", content=csv_text, headers={"content-type": "text/csv"})
    out = resp.json()
    assert out["errors"] == [] and out["committed"] is False
    assert [c["mode"] for c in out["converted"]] == ["qty_pnl_amount", "value_pnl_amount"]
    assert Decimal(out["converted"][1]["units"]) == 10


# -- parse-holdings ---------------------------------------------------------------------------------


async def test_parse_holdings_dime_style(settings: Settings, install_services: Any) -> None:
    llm = FakeLLM(
        [
            ParsedHoldings(
                rows=[
                    ParsedHolding(symbol="NVDA", value=549.96, pnl_pct=0.1452, market="US"),
                    ParsedHolding(symbol="AAPL", value=2000, pnl_pct=-0.031),
                ]
            )
        ]
    )
    install_services(portfolio_services(settings, llm))
    async with client() as c:
        resp = await c.post("/api/portfolios/parse-holdings", json={"text": DIME_TEXT, "market_hint": "US"})
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["mode"] == "native" and out["provider"] == "fake" and out["model"] == "fake-model"
        assert out["prompt_version"] == "portfolio_parse_holdings.v1" and out["usage"]["input_tokens"] > 0
        nvda, aapl = out["rows"]
        assert nvda["mode"] == "value_pnl_pct" and nvda["value"] == "549.96" and nvda["pnl_pct"] == "0.1452"
        assert aapl["market"] == "US" and aapl["pnl_pct"] == "-0.031"
        # the rows go straight into the preview
        preview = (await c.post("/api/portfolio/positions/preview", json={"positions": out["rows"]})).json()
    assert [r["status"] for r in preview["rows"]] == ["ok", "ok"]
    assert Decimal(preview["rows"][0]["quantity"]) == Decimal("549.96") / 180
    call = llm.calls[0]
    assert call.method == "structured" and call.tier == "quick" and call.purpose == "portfolio.parse_holdings"
    assert call.schema == "ParsedHoldings" and DIME_TEXT in call.messages[1].content


async def test_parse_holdings_streaming_style(settings: Settings, install_services: Any) -> None:
    # fallback path: the model answers with free text that holds the JSON
    llm = FakeLLM(
        [
            FakeTurn(
                text='```json\n{"rows": [{"symbol": "PTT", "quantity": "1,000", "avg_cost": 32.5, '
                '"pnl_pct": "+7.69%"}, {"symbol": "KBANK", "quantity": 100, "avg_cost": 150, '
                '"pnl_pct": -0.03}], "unparsed": ["SCB ---"]}\n```'
            )
        ]
    )
    install_services(portfolio_services(settings, llm))
    async with client() as c:
        resp = await c.post(
            "/api/portfolios/parse-holdings", json={"text": STREAMING_TEXT, "market_hint": "TH"}
        )
    out = resp.json()
    assert resp.status_code == 200 and out["mode"] == "fallback_parsed"
    ptt, kbank = out["rows"]
    assert (ptt["mode"], ptt["quantity"], ptt["avg_cost"], ptt["pnl_pct"]) == (
        "qty_cost",
        "1000.0",
        "32.5",
        None,
    )
    assert ptt["market"] == "TH" and kbank["avg_cost"] == "150.0"
    assert out["unparsed"] == ["SCB ---"]


async def test_parse_holdings_failed_parse_is_empty_not_500(
    settings: Settings, install_services: Any
) -> None:
    install_services(portfolio_services(settings, FakeLLM(["I could not read that, sorry."])))
    async with client() as c:
        resp = await c.post("/api/portfolios/parse-holdings", json={"text": "hello"})
    assert resp.status_code == 200 and resp.json()["mode"] == "failed" and resp.json()["rows"] == []


async def test_parse_holdings_with_a_picked_model(settings: Settings, install_services: Any) -> None:
    from pydantic import SecretStr

    keyed = settings.model_copy(update={"groq_api_key": SecretStr("k")})
    chosen = FakeLLM([ParsedHoldings(rows=[ParsedHolding(symbol="PTT", quantity=100, avg_cost=30)])])
    services = portfolio_services(keyed, FakeLLM([]))
    services.llm_for_choice = lambda provider, model: chosen
    install_services(services)
    async with client() as c:
        resp = await c.post(
            "/api/portfolios/parse-holdings",
            json={"text": "PTT 100 30", "llm": {"provider": "groq", "model": "openai/gpt-oss-20b"}},
        )
    assert resp.status_code == 200, resp.text
    assert chosen.calls[0].tier == "deep" and not services.llm.calls


async def test_parse_holdings_503_without_llm_and_text_limit(
    settings: Settings, install_services: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = install_services(portfolio_services(settings))

    def not_configured() -> Any:
        raise LLMNotConfigured("no LLM provider is configured for the quick tier")

    monkeypatch.setattr(services, "get_llm", not_configured)
    async with client() as c:
        resp = await c.post("/api/portfolios/parse-holdings", json={"text": STREAMING_TEXT})
        assert resp.status_code == 503
        assert resp.json()["error"]["type"] == "llm_not_configured"
        too_long = await c.post("/api/portfolios/parse-holdings", json={"text": "x" * 20_001})
        assert too_long.status_code == 422
