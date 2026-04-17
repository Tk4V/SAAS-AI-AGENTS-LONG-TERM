"""add user_oauth_credentials

Revision ID: 0002_user_oauth
Revises: 0001_initial
Create Date: 2026-04-16 00:02:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_user_oauth"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The git_provider_kind enum already exists from the initial migration;
    # we reuse it here without recreating it.
    git_provider_kind = postgresql.ENUM(
        "github",
        name="git_provider_kind",
        create_type=False,
    )

    op.create_table(
        "user_oauth_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", git_provider_kind, nullable=False),
        sa.Column("token_encrypted", sa.String(), nullable=False),
        sa.Column("scopes", sa.String(length=500), nullable=False, server_default=""),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_oauth_credentials"),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            name="uq_user_oauth_credentials_user_id_provider",
        ),
    )
    op.create_index(
        "ix_user_oauth_credentials_user_id",
        "user_oauth_credentials",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_oauth_credentials_user_id",
        table_name="user_oauth_credentials",
    )
    op.drop_table("user_oauth_credentials")
