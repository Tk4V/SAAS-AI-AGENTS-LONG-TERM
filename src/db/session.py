"""Async SQLAlchemy database wrapper.

`Database` owns the engine and the session factory and exposes the only
sanctioned way for the rest of the application to obtain a session. A single
module-level `db` instance acts as the process-wide singleton; tests can
build their own `Database(settings=...)` to point at a different DSN.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from asyncpg.exceptions import (
    InvalidAuthorizationSpecificationError,
    InvalidPasswordError,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import Settings, get_settings
from src.db.password_provider import SecretsManagerPasswordProvider

logger = structlog.get_logger("clyde.db.session")


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

        connect_args: dict[str, object] = {}
        self._password_provider: SecretsManagerPasswordProvider | None = None
        if self._settings.aws_secret_manager and self._settings.aws_region:
            self._password_provider = SecretsManagerPasswordProvider(
                self._settings.aws_secret_manager,
                self._settings.aws_region,
                ttl_seconds=self._settings.db_pool_recycle_sec,
            )
            connect_args["password"] = self._password_provider
            db_url = self._settings.database_url_no_password
        else:
            db_url = self._settings.database_url

        self._engine: AsyncEngine = create_async_engine(
            db_url,
            connect_args=connect_args,
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
        session = await self._open_session()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    @asynccontextmanager
    async def session_scope(self) -> AsyncIterator[AsyncSession]:
        """Standalone async context manager for use outside of FastAPI scope."""
        session = await self._open_session()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def _open_session(self) -> AsyncSession:
        """Open a session and force a real connection so auth errors surface here.

        On a Postgres auth failure the SM password cache is invalidated, the
        pool is disposed, and the connection is retried exactly once. Errors
        raised from user code after this method returns are not handled here.
        """
        session = self._sessionmaker()
        try:
            await session.connection()
        except Exception as exc:
            await session.close()
            if not self._is_auth_error(exc):
                raise
            logger.warning("auth error detected, refreshing credentials", error=str(exc))
            await self.refresh_credentials()
            session = self._sessionmaker()
            try:
                await session.connection()
            except Exception:
                await session.close()
                raise
        return session

    async def refresh_credentials(self) -> None:
        """Invalidate the SM password cache and dispose pooled connections.

        Called from ``_open_session`` on a Postgres auth failure so a rotated
        secret is picked up without waiting for ``pool_recycle`` or a restart.
        No-op when SM is not configured.
        """
        if self._password_provider is None:
            logger.debug("refresh_credentials called but SM not configured, no-op")
            return
        logger.warning("refreshing DB credentials")
        await self._password_provider.invalidate()
        await self._engine.dispose()

    @staticmethod
    def _is_auth_error(exc: BaseException) -> bool:
        if isinstance(exc, (InvalidPasswordError, InvalidAuthorizationSpecificationError)):
            return True
        if isinstance(exc, OperationalError):
            if "password authentication failed" in str(exc):
                return True
            orig = getattr(exc, "orig", None)
            if isinstance(orig, (InvalidPasswordError, InvalidAuthorizationSpecificationError)):
                return True
        return False

    async def dispose(self) -> None:
        """Close all pooled connections; called from the application shutdown hook."""
        await self._engine.dispose()


db = Database()
