from __future__ import annotations

from app.domain.models.provenance import new_record
from app.harness.provenance.citations import resolve_citations
from app.harness.provenance.number_check import cite_numbers, find_unverified_numbers

PRICE = new_record("data", {"close": 34.5, "sma50": 321.78, "closes": [1.0, 2.0, 34.5]}, source="yfinance")
SPECIFIC = new_record("data", 34.5, source="yfinance")
RATIO = new_record("compute", {"vol": 0.2849}, source="compute")


def test_cites_backed_numbers_with_most_specific_record() -> None:
    text = "PTT ปิดที่ 34.5 บาท ความผันผวน 28.49% และ 999.9 ไม่มีที่มา"
    cited = cite_numbers(text, [PRICE, SPECIFIC, RATIO])
    assert f"34.5[^{SPECIFIC.id}] บาท" in cited
    assert f"28.49%[^{RATIO.id}]" in cited
    assert "999.9 ไม่มีที่มา" in cited and cited.count("[^") == 2
    resolved = resolve_citations(cited, {PRICE.id, SPECIFIC.id, RATIO.id})
    assert "[1]" in resolved.markdown and len(resolved.citations) == 2


def test_existing_markers_are_kept_and_not_duplicated() -> None:
    text = f"close 34.5 [^{PRICE.id}] and SMA ($321.78)"
    cited = cite_numbers(text, [PRICE])
    assert cited.count(PRICE.id) == 2 and f"34.5 [^{PRICE.id}]" in cited
    assert cite_numbers("no numbers here", [PRICE]) == "no numbers here"


def test_period_labels_are_not_claims() -> None:
    text = "ราคาอยู่เหนือ SMA 50 วัน และ 200-day, ผลตอบแทน 3 month, ไตรมาส 2, FY 26, RSI 70, 1M/3M, SMA 50/200"
    assert find_unverified_numbers(text, []) == []
    assert find_unverified_numbers("กำไร 50 ล้าน", []) == ["50 ล้าน"]
    assert find_unverified_numbers("RSI (14) และ SMA(200), ATR 14", []) == []


def test_written_dates_are_not_claims() -> None:
    text = "ปิดวันที่ 24 กันยายน 2569, 24 ก.ย., as of Sep 24, 2026 และ 24 Sep"
    assert find_unverified_numbers(text, []) == []
    assert find_unverified_numbers("ราคา 24 บาท", []) == ["24"]


def test_implied_scale_for_numbers_with_units_in_headers() -> None:
    rev = new_record("data", {"revenue": 109417000000.0}, source="sec_edgar")
    assert find_unverified_numbers("| Revenue (USD bn) | 109.417 |", [rev]) == []
    assert find_unverified_numbers("| Revenue | 109.4 |", [rev]) == ["109.4"]  # 1 decimal: no implied scale
    assert find_unverified_numbers("| Revenue (USD mn) | 109,417 |", [rev]) == []


def test_marker_after_unit_word_counts_as_cited() -> None:
    text = f"รายได้ 109,417,000,000 ดอลลาร์สหรัฐ [^{PRICE.id}] และ 34.5, ต่อมา [^{PRICE.id}]"
    cited = cite_numbers(text, [SPECIFIC])
    assert "109,417,000,000 ดอลลาร์สหรัฐ [^" in cited and "109,417,000,000[^" not in cited
    assert f"34.5[^{SPECIFIC.id}]," in cited  # a comma ends the unit window


def test_implied_scale_cites_only_within_written_rounding() -> None:
    rev = new_record("data", {"revenue": 10_990_000_000.0}, source="sec_edgar")
    assert cite_numbers("Xiaomi 10,999 หยวน", [rev]) == "Xiaomi 10,999 หยวน"
    assert cite_numbers("| Revenue (USD mn) | 10,990 |", [rev]) == f"| Revenue (USD mn) | 10,990[^{rev.id}] |"
