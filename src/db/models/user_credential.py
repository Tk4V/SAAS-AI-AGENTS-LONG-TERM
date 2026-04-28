"""Per-user OAuth credentials for third-party integrations.

One row per (user, provider). The access token is stored Fernet-encrypted in
``token_encrypted``; the refresh token (when the provider issues one) lives
encrypted in ``refresh_token_encrypted``. Reading the plaintext is the
responsibility of ``TokenResolver``, which also handles auto-refresh.

``provider_account_id`` and ``account_label`` describe *which* account on
the provider the credential connects to (Slack workspace, Jira cloudId,
Discord user). ``raw_metadata`` is a JSONB escape hatch for per-provider
quirks (Salesforce instance URL, scopes returned outside the standard
``scope`` claim, etc.) so we do not balloon the schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, UserScopeMixin
from src.db.models.project import GitProviderKind


class UserOAuthCredential(Base, UUIDPrimaryKeyMixin, UserScopeMixin, TimestampMixin):
    __tablename__ = "user_oauth_credentials"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            name="uq_user_oauth_credentials_user_id_provider",
        ),
    )

    provider: Mapped[GitProviderKind] = mapped_column(
        Enum(
            GitProviderKind,
            name="git_provider_kind",
            create_type=False,
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
    )
    token_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
        default=dict,
    )
    scopes: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
