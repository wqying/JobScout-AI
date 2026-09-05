from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from app.db.session import get_worker_engine, get_worker_session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="Set RUN_INTEGRATION_TESTS=1 with local PostgreSQL and Redis running",
    ),
]


def test_worker_sessions_survive_consecutive_event_loops() -> None:
    get_worker_engine.cache_clear()

    async def query_database() -> int:
        async with get_worker_session_factory()() as session:
            return int(await session.scalar(text("SELECT 1")) or 0)

    first = asyncio.run(query_database())
    second = asyncio.run(query_database())
    engine = get_worker_engine()

    assert first == second == 1
    assert isinstance(engine.sync_engine.pool, NullPool)

    asyncio.run(engine.dispose())
    get_worker_engine.cache_clear()
