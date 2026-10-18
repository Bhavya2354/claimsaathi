"""Async engine and session factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import DatabaseSettings


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    url = settings.database_url.get_secret_value()
    options: dict[str, Any] = {"echo": settings.database_echo, "pool_pre_ping": True}
    if not url.startswith("sqlite"):
        options["pool_size"] = settings.database_pool_size
    engine = create_async_engine(url, **options)
    if url.startswith("sqlite"):
        # SQLite leaves foreign keys off by default, which would skip ON DELETE CASCADE.
        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(connection: Any, _record: Any) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def transaction(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessions() as session, session.begin():
        yield session
