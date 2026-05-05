"""Alembic environment configured for SQLAlchemy 2.x with an asyncpg engine.

The database URL is taken from application settings rather than alembic.ini.
We pass it directly to the engine and context instead of going through
config.set_main_option, because configparser chokes on percent-encoded
characters in passwords.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from src.config.settings import get_settings
from src.db.base import Base
from src.db import models  # noqa: F401  -- import models so Alembic sees them

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
target_metadata = Base.metadata


def _include_object(object, name, type_, reflected, compare_to):  # noqa: ANN001
    """Skip Alembic's own version table when autogenerating."""
    if type_ == "table" and name == "alembic_version":
        return False
    return True


def run_migrations_offline() -> None:
    """Generate SQL without connecting to a database, useful for review."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    connect_args: dict[str, object] = {}
    if settings.aws_secret_manager and settings.aws_region:
        from src.db.password_provider import SecretsManagerPasswordProvider

        provider = SecretsManagerPasswordProvider(settings.aws_secret_manager, settings.aws_region)
        connect_args["password"] = provider
        url = settings.database_url_no_password
    else:
        url = settings.database_url

    connectable = create_async_engine(url, poolclass=pool.NullPool, connect_args=connect_args)
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
