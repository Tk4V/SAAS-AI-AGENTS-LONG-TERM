"""Per-user OAuth credentials for third-party integrations.

One row per (user, provider). The token is stored Fernet-encrypted in
`token_encrypted`; the cipher lives in `src.common.crypto`. Reading the
plaintext token is the responsibility of `OAuthService.get_token`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, String, UniqueConstraint, func
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
        Enum(GitProviderKind, name="git_provider_kind", create_type=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    token_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    scopes: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
