"""SQLAlchemy declarative base and reusable column mixins.

Every ORM model in `src/db/models` should inherit from `Base`. The mixins
encode two project-wide invariants documented in the brief:

- every domain table carries a `user_id` column for multi-tenant isolation,
- every table carries `created_at` / `updated_at` timestamps in UTC.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Naming convention keeps Alembic autogenerate output deterministic. Without it,
# constraint names depend on dialect defaults and produce noisy diffs.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    """Generates UUID v4 primary keys client-side so we do not depend on uuid-ossp."""

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UserScopeMixin:
    """Adds a user_id column. Django DRF owns the auth_user table, so we keep
    a soft reference rather than a hard foreign key. All queries must filter by
    user_id to enforce tenant isolation; row-level security can be layered on
    top later as defence in depth.
    """

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
