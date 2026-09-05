from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.api.dependencies import local_day_window
from app.api.errors import AppError
from app.discovery.local_day import LocalDayWindow


@pytest.mark.parametrize("naive_field", ["start", "end", "now"])
def test_local_day_rejects_naive_datetimes(naive_field: str) -> None:
    values = {
        "start": datetime(2026, 8, 29, 0, 0, tzinfo=UTC),
        "end": datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        "now": datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
    }
    values[naive_field] = values[naive_field].replace(tzinfo=None)

    with pytest.raises(ValueError, match="must include an offset"):
        LocalDayWindow.validated(**values)


@pytest.mark.parametrize(
    ("start", "end", "expected_duration"),
    [
        (
            datetime(2026, 3, 8, 0, 0, tzinfo=timezone(timedelta(hours=-5))),
            datetime(2026, 3, 9, 0, 0, tzinfo=timezone(timedelta(hours=-4))),
            timedelta(hours=23),
        ),
        (
            datetime(2026, 11, 1, 0, 0, tzinfo=timezone(timedelta(hours=-4))),
            datetime(2026, 11, 2, 0, 0, tzinfo=timezone(timedelta(hours=-5))),
            timedelta(hours=25),
        ),
    ],
)
def test_local_day_accepts_daylight_saving_length_days(
    start: datetime,
    end: datetime,
    expected_duration: timedelta,
) -> None:
    now = start.astimezone(UTC) + timedelta(hours=12)

    window = LocalDayWindow.validated(start, end, now=now)

    assert window.start.tzinfo is UTC
    assert window.end.tzinfo is UTC
    assert window.end - window.start == expected_duration


@pytest.mark.parametrize("duration_hours", [21, 27])
def test_local_day_rejects_implausible_calendar_day_spans(duration_hours: int) -> None:
    start = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="must span one calendar day"):
        LocalDayWindow.validated(
            start,
            start + timedelta(hours=duration_hours),
            now=start + timedelta(hours=1),
        )


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 8, 28, 23, 59, 59, tzinfo=UTC),
        datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
    ],
)
def test_local_day_requires_current_instant_in_half_open_window(now: datetime) -> None:
    with pytest.raises(ValueError, match="must fall inside"):
        LocalDayWindow.validated(
            datetime(2026, 8, 29, 0, 0, tzinfo=UTC),
            datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
            now=now,
        )


def test_local_day_includes_start_instant() -> None:
    start = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)

    window = LocalDayWindow.validated(
        start,
        datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        now=start,
    )

    assert window.start == start


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (None, None),
        ("not-a-date", "2026-08-30T00:00:00Z"),
        ("2026-08-29T00:00:00", "2026-08-30T00:00:00"),
    ],
)
def test_local_day_header_dependency_returns_stable_error(
    start: str | None,
    end: str | None,
) -> None:
    with pytest.raises(AppError) as error:
        local_day_window(start, end)

    assert error.value.code == "LOCAL_DAY_CONTEXT_INVALID"
    assert error.value.status_code == 400


def test_local_day_header_dependency_accepts_current_utc_day() -> None:
    now = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    window = local_day_window(start.isoformat(), end.isoformat())

    assert window == LocalDayWindow(start=start, end=end)
