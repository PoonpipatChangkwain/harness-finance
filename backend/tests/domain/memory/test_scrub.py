"""(P17) ``scrub_text``: every regex, Thai + English, deterministic, never leaks the original value."""

from __future__ import annotations

import pytest

from app.domain.memory.scrub import PII_KINDS, has_pii, scrub_text


def kinds(text: str) -> list[str]:
    return sorted(h.kind for h in scrub_text(text)[1])


@pytest.mark.parametrize(
    ("text", "clean", "kind"),
    [
        ("ติดต่อ john.doe+x@mail.co.th ได้", "ติดต่อ [email] ได้", "email"),
        ("contact me at a_b@example.com", "contact me at [email]", "email"),
        ("เลขบัตร 1234567890123 นะ", "เลขบัตร [id] นะ", "thai_id"),
        ("ID 1-2345-67890-12-3", "ID [id]", "thai_id"),
        ("โทร 081-234-5678", "โทร [phone]", "phone"),
        ("call 0812345678 now", "call [phone] now", "phone"),
        ("+66 81 234 5678", "[phone]", "phone"),
        ("office 02-123-4567", "office [phone]", "phone"),
        ("บัญชี 123-4-56789-0 กสิกร", "บัญชี [account] กสิกร", "account"),
        ("acct 1234567890", "acct [account]", "account"),
        ("acct 123456789012", "acct [account]", "account"),
    ],
)
def test_pii_patterns(text: str, clean: str, kind: str) -> None:
    out, hits = scrub_text(text)
    assert out == clean
    assert [h.kind for h in hits] == [kind]
    assert kind in PII_KINDS
    assert has_pii(hits)


@pytest.mark.parametrize(
    ("text", "clean"),
    [
        ("ต้นทุน ฿1,250,000", "ต้นทุน >THB 1m"),
        ("งบ 50,000 บาท", "งบ <THB 100k"),
        ("เก็บ 2 ล้านบาท", "เก็บ >THB 1m"),
        ("ลงทุน 5 แสนบาท", "ลงทุน THB 100k-1m"),
        ("THB 250,000 budget", "THB 100k-1m budget"),
        ("bought $5k of it", "bought USD 3k-30k of it"),
        ("USD 40,000 account", ">USD 30k account"),
        ("only 500 dollars", "only <USD 3k"),
    ],
)
def test_amounts_become_buckets(text: str, clean: str) -> None:
    out, hits = scrub_text(text)
    assert out == clean
    assert [h.kind for h in hits] == ["amount"]
    assert not has_pii(hits)


@pytest.mark.parametrize(
    ("text", "clean"),
    [
        ("17%", "15%"),
        ("weight 23% of portfolio", "weight 25% of portfolio"),
        ("ลดลง 12.5 %", "ลดลง 15%"),
        ("risk 1% per trade", "risk <5% per trade"),
        ("cap 20%", "cap 20%"),
        ("0%", "0%"),
    ],
)
def test_percent_rounds_to_five(text: str, clean: str) -> None:
    assert scrub_text(text)[0] == clean


def test_share_quantities_are_hidden() -> None:
    assert scrub_text("ถือ 2,000 หุ้น")[0] == "ถือ a position"
    assert scrub_text("bought 150 Shares")[0] == "bought a position"


@pytest.mark.parametrize(
    ("text", "clean"),
    [
        ("avoid NVDA here", "avoid US tech large-cap here"),
        ("KBANK dividend", "Thai bank large-cap dividend"),
        ("ผู้ใช้ซื้อPTTเยอะ", "ผู้ใช้ซื้อThai energy large-capเยอะ"),
        ("PTT.BK fell", "Thai energy large-cap fell"),
        ("ZZZQ squeeze", "a single stock squeeze"),
        ("use SPY or QQQ", "use SPY or QQQ"),
        ("hedge with GLD and XAUUSD", "hedge with GLD and XAUUSD"),
        ("RSI and MACD on the SET", "RSI and MACD on the SET"),
        ("in USD terms, ETF first", "in USD terms, ETF first"),
    ],
)
def test_ticker_allowlist(text: str, clean: str) -> None:
    assert scrub_text(text)[0] == clean


def test_holding_line_combines_passes_without_rematching() -> None:
    out, hits = scrub_text("ซื้อ KBANK 2,000 หุ้น ต้นทุน ฿1,250,000 คิดเป็น 17% ของพอร์ต")
    assert out == "ซื้อ Thai bank large-cap a position ต้นทุน >THB 1m คิดเป็น 15% ของพอร์ต"
    assert sorted(h.kind for h in hits) == ["amount", "percent", "quantity", "ticker"]
    assert all("KBANK" not in h.replacement and "1,250,000" not in h.replacement for h in hits)


def test_generic_lesson_is_untouched_and_deterministic() -> None:
    text = "Confirm the investor's horizon before sizing a speculative position; REVIEW drawdown history."
    assert scrub_text(text) == (text, [])
    assert scrub_text(text) == scrub_text(text)
    assert kinds("Never put a 13-digit number like 1234567890123 here") == ["thai_id"]
