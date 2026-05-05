"""Add user_tool_configs table for per-user tool preferences.

Revision ID: 0019_user_tool_configs
Revises: 0018_agent_tool_mcp_configs
Create Date: 2026-05-05 00:00:00.000000

Adds per-user overrides on top of the system agent_tool_configs defaults.
A row with is_enabled=False suppresses that tool for the user; no row means
the system default applies.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_user_tool_configs"
down_revision: Union[str, None] = "0018_agent_tool_mcp_configs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_tool_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_name", sa.String(64), nullable=False),
        sa.Column("subagent_role", sa.String(64), nullable=True),
        sa.Column("tool_pattern", sa.String(256), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_user_tool_configs"),
        sa.UniqueConstraint(
            "user_id", "agent_name", "subagent_role", "tool_pattern",
            name="uq_user_tool_configs_user_agent_role_pattern",
        ),
    )
    op.create_index(
        "ix_user_tool_configs_user_agent",
        "user_tool_configs",
        ["user_id", "agent_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_tool_configs_user_agent", table_name="user_tool_configs")
    op.drop_table("user_tool_configs")
