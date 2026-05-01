"""drop dead schema: prompt_blocks table and project_repos.oauth_token_encrypted

Revision ID: 0016_drop_dead_schema
Revises: 0015_drop_user_oauth_credentials
Create Date: 2026-05-01 16:45:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_drop_dead_schema"
down_revision: Union[str, None] = "0015_drop_user_oauth_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("prompt_blocks")
    op.drop_column("project_repos", "oauth_token_encrypted")


def downgrade() -> None:
    op.add_column(
        "project_repos",
        sa.Column("oauth_token_encrypted", sa.String(), nullable=True),
    )
    # prompt_blocks recreate kept minimal — historical seed data is not restored.
    op.create_table(
        "prompt_blocks",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(), nullable=False, unique=True),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("agent_role", sa.String(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
