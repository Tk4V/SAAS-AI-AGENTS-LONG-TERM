"""Async SQLAlchemy database wrapper.

`Database` owns the engine and the session factory and exposes the only
sanctioned way for the rest of the application to obtain a session. A single
module-level `db` instance acts as the process-wide singleton; tests can
build their own `Database(settings=...)` to point at a different DSN.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import Settings, get_settings


class Database:
    """Process-wide async database gateway backed by SQLAlchemy.

    Owns the ``AsyncEngine`` and an ``async_sessionmaker``, and provides
    two ways to obtain a scoped session: ``get_session`` for FastAPI
    dependency injection and ``session_scope`` for standalone use
    (CLI scripts, background tasks, tests).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """Create the engine and session factory from application settings.

        Args:
            settings: Optional settings override; defaults to ``get_settings()``.
                Useful in tests to point at an isolated database.
        """
        self._settings = settings or get_settings()
        self._engine: AsyncEngine = create_async_engine(
            self._settings.database_url,
            pool_size=self._settings.db_pool_size,
            max_overflow=self._settings.db_max_overflow,
            pool_recycle=self._settings.db_pool_recycle_sec,
            pool_pre_ping=True,
            echo=self._settings.debug,
        )
        self._sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        """The underlying ``AsyncEngine`` instance."""
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        """The configured ``async_sessionmaker`` bound to the engine."""
        return self._sessionmaker

    async def get_session(self) -> AsyncIterator[AsyncSession]:
        """FastAPI dependency that commits on success and rolls back on error."""
        async with self._sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def session_scope(self) -> AsyncIterator[AsyncSession]:
        """Standalone async context manager for use outside of FastAPI scope."""
        async with self._sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        """Close all pooled connections; called from the application shutdown hook."""
        await self._engine.dispose()


db = Database()
