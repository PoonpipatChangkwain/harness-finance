"""ForexFactory feed parsing, the calendar window filter, and instrument → currencies."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.compute.risk import realized_r
from app.domain.data.base import Transient
from app.domain.data.providers.forexfactory_provider import parse_feed
from app.domain.models.horizon import horizon, time_stop_at
from app.domain.models.symbol import normalize_symbol
from app.domain.tools.calendar import event_currencies, upcoming

ROWS = [  # shape of nfs.faireconomy.media/ff_calendar_thisweek.json
    {"title": "CPI m/m", "country": "USD", "date": "2026-09-28T08:30:00-04:00", "impact": "High",
     "forecast": "0.3%", "previous": "0.2%"},
    {"title": "BOJ Summary of Opinions", "country": "JPY", "date": "2026-09-28T19:50:00-04:00",
     "impact": "Low", "forecast": "", "previous": ""},
    {"title": "GDP q/q", "country": "GBP", "date": "2026-09-29T02:00:00-04:00", "impact": "Medium",
     "forecast": "0.1%", "previous": "0.2%"},
    {"title": "Bank Holiday", "country": "CNY", "date": "2026-10-01T00:00:00-04:00", "impact": "Holiday"},
    {"title": "broken", "country": "EUR", "date": "not a date", "impact": "High"},
    {"title": "unknown impact", "country": "EUR", "date": "2026-09-28T08:30:00-04:00",
     "impact": "Non-Economic"},
]  # fmt: skip


def test_parse_feed_keeps_valid_rows_with_timezone() -> None:
    events = parse_feed(ROWS)
    assert [e.title for e in events] == ["CPI m/m", "BOJ Summary of Opinions", "GDP q/q", "Bank Holiday"]
    cpi = events[0]
    assert cpi.impact == "high" and cpi.currency == "USD" and cpi.forecast == "0.3%" and cpi.actual is None
    assert cpi.at == datetime(2026, 9, 28, 12, 30, tzinfo=UTC)
    with pytest.raises(Transient):
        parse_feed("<html>Request Denied</html>")


def test_upcoming_filters_currency_window_and_impact() -> None:
    events = parse_feed(ROWS)
    now = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)
    gbpjpy = upcoming(events, ["GBP", "JPY"], now=now, hours_ahead=24)
    assert [e.title for e in gbpjpy] == ["GDP q/q"]  # JPY release is low impact; USD CPI is not GBP/JPY
    assert [e.title for e in upcoming(events, ["USD"], now=now, hours_ahead=24)] == ["CPI m/m"]
    assert upcoming(events, ["USD"], now=now, hours_ahead=1) == []
    assert [e.title for e in upcoming(events, ["JPY"], now=now, hours_ahead=24, min_impact="low")] == [
        "BOJ Summary of Opinions"
    ]


def test_event_currencies() -> None:
    assert event_currencies(normalize_symbol("GBPJPY")) == ["GBP", "JPY"]
    assert event_currencies(normalize_symbol("PTT")) == ["THB", "USD"]
    assert event_currencies(normalize_symbol("AAPL")) == ["USD"]


def test_horizons_and_time_stop() -> None:
    assert horizon("intraday").interval == "15m" and horizon(None).name == "swing"
    start = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
    assert time_stop_at(start, "15m", 16) == datetime(2026, 9, 28, 13, 0, tzinfo=UTC)


def test_realized_r() -> None:
    from decimal import Decimal as D

    assert realized_r(D("100"), D("98"), D("104"), "long").value == D("2")
    assert realized_r(D("100"), D("102"), D("103"), "short").value == D("-1.5")
    assert realized_r(D("100"), D("100"), D("101"), "long").value is None
