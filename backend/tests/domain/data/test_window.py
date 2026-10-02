from __future__ import annotations

from datetime import UTC, datetime

from app.domain.data.window import coverage_gap, in_window, to_utc


def test_in_window_half_open_upper_bound() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 10, tzinfo=UTC)
    assert in_window(datetime(2026, 1, 1, 0, 0, tzinfo=UTC), start, end) is True
    assert in_window(datetime(2026, 1, 10, 23, 59, tzinfo=UTC), start, end) is True
    assert in_window(datetime(2026, 1, 11, 0, 0, tzinfo=UTC), start, end) is False
    assert in_window(datetime(2025, 12, 31, 23, 59, tzinfo=UTC), start, end) is False


def test_in_window_naive_datetime_assumed_utc() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 10, tzinfo=UTC)
    assert in_window(datetime(2026, 1, 5), start, end) is True


def test_undated_item_kept_only_for_live_window() -> None:
    now = datetime.now(UTC)
    live_start = now.replace(day=1)
    assert in_window(None, live_start, now) is True

    backtest_end = datetime(2020, 1, 10, tzinfo=UTC)
    backtest_start = datetime(2020, 1, 1, tzinfo=UTC)
    assert in_window(None, backtest_start, backtest_end) is False


def test_to_utc_normalizes_naive_and_aware() -> None:
    naive = datetime(2026, 1, 1, 12, 0)
    assert to_utc(naive).tzinfo is UTC


def test_coverage_gap_none_when_window_is_fully_observed() -> None:
    dates = [datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)]
    result = coverage_gap(
        dates, start_date=dates[0].date(), end_date=dates[1].date(), source="x", subject="news"
    )
    assert result is None


def test_coverage_gap_reports_recent_only_feed() -> None:
    dates = [datetime.now(UTC)]
    result = coverage_gap(
        dates,
        start_date=datetime(2020, 1, 1, tzinfo=UTC).date(),
        end_date=datetime.now(UTC).date(),
        source="reddit",
        subject="posts",
    )
    assert result is not None
    assert "reddit" in result
