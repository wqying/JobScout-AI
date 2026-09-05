import os
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.base import Base


@pytest_asyncio.fixture
async def isolated_session() -> AsyncIterator[AsyncSession]:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        raise RuntimeError("Integration database fixture used without RUN_INTEGRATION_TESTS=1")
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        table_names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
        await connection.execute(text(f"TRUNCATE {table_names} CASCADE"))
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()
