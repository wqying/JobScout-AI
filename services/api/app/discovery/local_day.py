from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class LocalDayWindow:
    """One browser-local calendar day represented by exact UTC instants.

    The browser constructs local midnight and the following local midnight with its system clock.
    Keeping only those instants avoids making the API container guess the host timezone and keeps
    daylight-saving transitions explicit: a local day may be 23, 24, or 25 hours long.
    """

    start: datetime
    end: datetime

    @classmethod
    def validated(
        cls,
        start: datetime,
        end: datetime,
        *,
        now: datetime,
    ) -> LocalDayWindow:
        if not _is_aware(start) or not _is_aware(end) or not _is_aware(now):
            raise ValueError("Local-day boundaries and the current time must include an offset")

        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        now_utc = now.astimezone(UTC)
        duration = end_utc - start_utc
        if not timedelta(hours=22) <= duration <= timedelta(hours=26):
            raise ValueError("Local-day boundaries must span one calendar day")
        if not start_utc <= now_utc < end_utc:
            raise ValueError("The current time must fall inside the supplied local day")
        return cls(start=start_utc, end=end_utc)


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
