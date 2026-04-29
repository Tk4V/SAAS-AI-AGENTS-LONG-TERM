"""Append-only audit log for credential operations."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, UserScopeMixin, UUIDPrimaryKeyMixin


class CredentialEventType(str, enum.Enum):
    """Audit event kinds. Stored as a free string so adding new kinds does
    not require a migration.
    """

    CREATED = "created"
    READ = "read"
    RESOLVED = "resolved"
    DELETED = "deleted"


class CredentialEvent(Base, UUIDPrimaryKeyMixin, UserScopeMixin):
    __tablename__ = "credential_events"

    credential_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
