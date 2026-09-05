from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.session import get_session_factory
from app.discovery.local_day import LocalDayWindow


async def database_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


def local_day_window(
    start_value: Annotated[
        str | None,
        Header(alias="X-JobScout-Local-Day-Start"),
    ] = None,
    end_value: Annotated[
        str | None,
        Header(alias="X-JobScout-Local-Day-End"),
    ] = None,
) -> LocalDayWindow:
    """Read browser-computed local-midnight instants without guessing its timezone."""

    try:
        if start_value is None or end_value is None:
            raise ValueError("Both local-day headers are required")
        start = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_value.replace("Z", "+00:00"))
        return LocalDayWindow.validated(start, end, now=datetime.now(UTC))
    except (TypeError, ValueError) as exc:
        raise AppError(
            "LOCAL_DAY_CONTEXT_INVALID",
            "Refresh the page so JobScout can use the current day from your system clock.",
            status_code=400,
        ) from exc
