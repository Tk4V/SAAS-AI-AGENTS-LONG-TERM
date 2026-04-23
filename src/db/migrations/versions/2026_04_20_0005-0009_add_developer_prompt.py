"""add developer prompt block

Revision ID: 0009_developer_prompt
Revises: 0008_pipeline_v2_prompts
Create Date: 2026-04-20 14:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0009_developer_prompt"
down_revision: Union[str, None] = "0008_pipeline_v2_prompts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    prompt_blocks = sa.table(
        "prompt_blocks",
        sa.column("id", sa.dialects.postgresql.UUID),
        sa.column("key", sa.String),
        sa.column("content", sa.String),
        sa.column("category", sa.String),
        sa.column("agent_role", sa.String),
        sa.column("priority", sa.Integer),
        sa.column("is_active", sa.Boolean),
    )

    content = (
        "Your role: Developer.\n"
        "You explore codebases, plan changes, write code, and verify your work.\n"
        "Read files to understand context, then make targeted edits.\n"
        "Always verify after editing. Call done() when finished."
    )

    op.bulk_insert(prompt_blocks, [{
        "id": str(uuid4()), "key": "developer_role", "content": content,
        "category": "role", "agent_role": "developer", "priority": 5, "is_active": True,
    }])


def downgrade() -> None:
    op.execute("DELETE FROM prompt_blocks WHERE key = 'developer_role'")
