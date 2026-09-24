"""SQLAlchemy 2.x foundation.

The API uses the async engine; Celery workers are synchronous and use the sync
engine. Both share DATABASE_URL (psycopg 3 supports either mode). Engines are
created lazily so the app can boot without a database.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

# Deterministic constraint names keep Alembic migrations stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_async_engine: AsyncEngine | None = None
_async_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_sync_engine: Engine | None = None
_sync_sessionmaker: sessionmaker[Session] | None = None


def get_async_engine() -> AsyncEngine:
    global _async_engine, _async_sessionmaker
    if _async_engine is None:
        _async_engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        _async_sessionmaker = async_sessionmaker(_async_engine, expire_on_commit=False)
    return _async_engine


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request."""
    get_async_engine()
    assert _async_sessionmaker is not None
    async with _async_sessionmaker() as session:
        yield session


def get_sync_engine() -> Engine:
    global _sync_engine, _sync_sessionmaker
    if _sync_engine is None:
        _sync_engine = create_engine(get_settings().database_url, pool_pre_ping=True)
        _sync_sessionmaker = sessionmaker(_sync_engine, expire_on_commit=False)
    return _sync_engine


@contextmanager
def sync_session() -> Iterator[Session]:
    """For Celery tasks. Commits on success, rolls back on error."""
    get_sync_engine()
    assert _sync_sessionmaker is not None
    with _sync_sessionmaker() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


async def dispose_engines() -> None:
    global _async_engine, _async_sessionmaker, _sync_engine, _sync_sessionmaker
    if _async_engine is not None:
        await _async_engine.dispose()
    if _sync_engine is not None:
        _sync_engine.dispose()
    _async_engine = _async_sessionmaker = _sync_engine = _sync_sessionmaker = None
