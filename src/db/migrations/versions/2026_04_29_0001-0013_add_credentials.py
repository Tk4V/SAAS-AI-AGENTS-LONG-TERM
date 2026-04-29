"""add credentials and credential_events tables

Revision ID: 0013_add_credentials
Revises: 0012_rename_provider_kind
Create Date: 2026-04-29 00:00:00.000000

Introduces a unified credential storage that will absorb the OAuth path in a
later step. ``credentials`` holds the encrypted payload plus non-secret
metadata; ``credential_events`` is an append-only audit log.

The kind column is a Postgres enum so adding new kinds (oauth, basic, AWS,
etc.) later is a single ``ALTER TYPE ... ADD VALUE``. The audit event_type is
deliberately kept as a free string column for the same reason — adding a new
event kind should not require a migration.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_add_credentials"
down_revision: Union[str, None] = "0012_rename_provider_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CREDENTIAL_KIND_ENUM = "credential_kind"


def upgrade() -> None:
    credential_kind = postgresql.ENUM(
        "bearer",
        name=CREDENTIAL_KIND_ENUM,
        create_type=True,
    )
    credential_kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(
                "bearer",
                name=CREDENTIAL_KIND_ENUM,
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("preview", sa.String(length=64), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_credentials"),
    )
    op.create_index(
        "ix_credentials_user_id",
        "credentials",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_credentials_user_id_deleted_at",
        "credentials",
        ["user_id", "deleted_at"],
        unique=False,
    )

    op.create_table(
        "credential_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            name="fk_credential_events_credential_id_credentials",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_credential_events"),
    )
    op.create_index(
        "ix_credential_events_credential_id",
        "credential_events",
        ["credential_id"],
        unique=False,
    )
    op.create_index(
        "ix_credential_events_user_id",
        "credential_events",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_credential_events_user_id", table_name="credential_events")
    op.drop_index("ix_credential_events_credential_id", table_name="credential_events")
    op.drop_table("credential_events")

    op.drop_index("ix_credentials_user_id_deleted_at", table_name="credentials")
    op.drop_index("ix_credentials_user_id", table_name="credentials")
    op.drop_table("credentials")

    credential_kind = postgresql.ENUM(name=CREDENTIAL_KIND_ENUM)
    credential_kind.drop(op.get_bind(), checkfirst=True)
