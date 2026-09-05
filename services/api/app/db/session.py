from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@lru_cache(maxsize=1)
def get_worker_engine() -> AsyncEngine:
    """Return an engine whose connections cannot cross Celery task event loops.

    Celery's synchronous prefork tasks call ``asyncio.run()`` for each delivery. A normal
    SQLAlchemy pool can retain an asyncpg connection created by an earlier event loop and hand it
    to a later one. NullPool closes each connection with its session instead.
    """

    return create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        poolclass=NullPool,
    )


def get_worker_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_worker_engine(), expire_on_commit=False)
