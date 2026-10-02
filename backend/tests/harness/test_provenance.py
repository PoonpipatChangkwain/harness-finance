from __future__ import annotations

import pytest

from app.domain.models.provenance import ProvenanceRecord, new_record
from app.harness.provenance.citations import resolve_citations, strip_citation_markers
from app.harness.provenance.number_check import find_unverified_numbers
from app.harness.provenance.store import ProvenanceStore
from app.harness.state.memory import MemoryStore


def _records() -> list[ProvenanceRecord]:
    return [
        new_record("data", 34.25, source="yfinance", unit="THB"),
        new_record("data", 1_234.5, source="yfinance", unit="USD"),
        new_record("compute", 0.123, source="compute", unit="ratio"),
        new_record("data", 1_234_567_890, source="sec_edgar", unit="USD"),
        new_record("data", 3_400_000, source="set", unit="THB"),
        new_record("compute", {"rsi14": 55.23, "sma50": 187.456}, source="compute", params={"window": 14}),
        new_record("data", [{"close": 101.0}, {"close": 99.5}], source="yfinance"),
        new_record("compute", -0.052, source="compute", unit="ratio"),
        new_record("data", 45, source="yfinance", unit="USD"),
        new_record("data", 2_500_000_000_000, source="bot", unit="THB"),
    ]


@pytest.mark.parametrize(
    ("text", "unverified"),
    [
        # English — verified
        ("PTT closed at 34.25 THB.", []),
        ("Price is 34.3", []),  # rounding of 34.25
        ("Revenue was $1,234.5 last quarter", []),
        ("Revenue was 1,234 dollars", []),  # 0.5% relative tolerance
        ("Margin of 12.3%", []),  # 12.3% ↔ 0.123
        ("Revenue 1.2B", []),  # 1,234,567,890
        ("Revenue 1.23 billion", []),
        ("RSI(14) is 55.2", []),  # window=14 from params, 55.2 ≈ 55.23
        ("SMA50 at 187.46", []),
        ("Closed at 99.5 after 101", []),  # values inside a list of dicts
        ("Shares fell 5.2%", []),  # sign-agnostic vs -0.052
        ("Target $45", []),
        # Thai — verified
        ("ราคาปิด 34.25 บาท", []),
        ("รายได้ 3.4 ล้าน บาท", []),
        ("ปิดที่34.25บาท", []),  # no spaces around Thai
        ("GDP 2.5 ล้านล้าน บาท", []),
        # skipped (not claims)
        ("In 2025 the company, and in 2569 (พ.ศ.)", []),
        ("As of 2026-09-24 at 10:30, see 24/09/2026", []),
        ("1. First point\n2. Second point", []),
        ("We checked 3 sources", []),
        ("PTT closed at 34.25 [^prov_01ABC] and [1]", []),
        # unverified
        ("PTT closed at 36.80 THB", ["36.80"]),
        ("Margin of 47%", ["47%"]),
        ("Revenue $9,999", ["$9,999"]),
        ("รายได้ 7.8 ล้าน", ["7.8 ล้าน"]),
        ("Net income 2.1B and price 34.25", ["2.1B"]),
        ("ราคา ฿50", ["฿50"]),
        ("P/E of 17.5x", ["17.5"]),
    ],
)
def test_number_check(text: str, unverified: list[str]) -> None:
    assert find_unverified_numbers(text, _records()) == unverified


def test_citations() -> None:
    md = "PTT 34.25[^prov_A1] and 12.3%[^prov_B2], again[^prov_A1] bogus[^prov_ZZ]"
    result = resolve_citations(md, known_ids={"prov_A1", "prov_B2"})
    assert result.markdown == "PTT 34.25[1] and 12.3%[2], again[1] bogus"
    assert [(c.marker, c.provenance_id) for c in result.citations] == [(1, "prov_A1"), (2, "prov_B2")]
    assert result.unknown_ids == ["prov_ZZ"]
    assert strip_citation_markers("a[^prov_X] b[3]") == "a b"


@pytest.mark.usefixtures("db")
async def test_store_upsert_list_and_lineage() -> None:
    store = ProvenanceStore()
    price = new_record("data", 34.5, source="yfinance")
    sma = new_record("compute", 33.0, source="compute", inputs=[price.id])
    signal = new_record("compute", "bullish", source="compute", inputs=[sma.id, "prov_missing"])
    await store.add_many([price, sma, signal], "run_X")
    await store.add_many([price.model_copy(update={"unit": "THB"})], "run_X")  # upsert

    assert (await store.get(price.id)).unit == "THB"  # type: ignore[union-attr]
    assert {r.id for r in await store.list("run_X")} == {price.id, sma.id, signal.id}

    tree = await store.lineage(signal.id)
    assert tree is not None
    assert tree.missing_inputs == ["prov_missing"]
    assert tree.inputs[0].record.id == sma.id
    assert tree.inputs[0].inputs[0].record.id == price.id
    shallow = await store.lineage(signal.id, depth=1)
    assert shallow is not None and shallow.inputs[0].inputs == []


@pytest.mark.usefixtures("db")
async def test_memory_store() -> None:
    mem = MemoryStore()
    await mem.put("user", "risk_profile", {"level": "moderate"})
    await mem.put("user", "risk_profile", {"level": "aggressive"})
    await mem.put("decisions", "PTT.BK/2026-09-24", {"rating": "Hold"})
    await mem.put("decisions", "AOT.BK/2026-09-24", {"rating": "Buy"})
    item = await mem.get("user", "risk_profile")
    assert item is not None and item.value == {"level": "aggressive"}
    assert [i.key for i in await mem.search("decisions", "PTT")] == ["PTT.BK/2026-09-24"]
    assert await mem.delete("user", "risk_profile")
    assert await mem.get("user", "risk_profile") is None
