"""Credential ORM model.

A credential is a user-owned secret with a kind-specific encrypted payload and
a non-secret metadata bag. Soft-deleted rows stay in the table so the audit
log keeps a referenceable target after deletion.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UserScopeMixin, UUIDPrimaryKeyMixin


class CredentialKind(str, enum.Enum):
    """Credential kinds. Extending this requires an enum migration."""

    BEARER = "bearer"
    OAUTH = "oauth"


class Credential(Base, UUIDPrimaryKeyMixin, UserScopeMixin, TimestampMixin):
    __tablename__ = "credentials"

    kind: Mapped[CredentialKind] = mapped_column(
        Enum(
            CredentialKind,
            name="credential_kind",
            create_type=False,
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    preview: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
        default=dict,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
