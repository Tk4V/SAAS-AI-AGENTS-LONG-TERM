"""add token metadata columns to user_oauth_credentials

Revision ID: 0011_oauth_token_metadata
Revises: 0010_remove_qa_engineer_prompt
Create Date: 2026-04-28 00:00:00.000000

Adds columns the new OAuth framework writes alongside the access token:

- ``refresh_token_encrypted`` — encrypted refresh token, NULL for providers
  that issue non-rotating tokens (e.g. GitHub OAuth Apps).
- ``expires_at`` — absolute expiry in UTC, drives auto-refresh in
  TokenResolver. NULL for non-expiring tokens.
- ``provider_account_id`` — Slack team_id / Atlassian cloudId / Discord
  user id; identifies which sub-account on the provider this credential is
  attached to. NULL for providers without such a notion.
- ``account_label`` — human-readable label shown in the integrations UI
  (e.g. "Acme workspace", "vasyl@intrepide.ai"). NULL when not yet
  resolved.
- ``raw_metadata`` — JSONB pocket for provider-specific quirks
  (Salesforce instance_url, OAuth scopes-not-in-scope-claim, etc.) so we
  do not bloat the schema with one column per quirk.

All columns are nullable. Existing rows survive without rewrite.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_oauth_token_metadata"
down_revision: Union[str, None] = "0010_remove_qa_engineer_prompt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_oauth_credentials",
        sa.Column("refresh_token_encrypted", sa.String(), nullable=True),
    )
    op.add_column(
        "user_oauth_credentials",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_oauth_credentials",
        sa.Column("provider_account_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "user_oauth_credentials",
        sa.Column("account_label", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "user_oauth_credentials",
        sa.Column(
            "raw_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("user_oauth_credentials", "raw_metadata")
    op.drop_column("user_oauth_credentials", "account_label")
    op.drop_column("user_oauth_credentials", "provider_account_id")
    op.drop_column("user_oauth_credentials", "expires_at")
    op.drop_column("user_oauth_credentials", "refresh_token_encrypted")
