"""add prompt blocks for researcher, planner, implementer, publisher

Revision ID: 0008_pipeline_v2_prompts
Revises: 0007_coder_prompt
Create Date: 2026-04-20 13:30:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0008_pipeline_v2_prompts"
down_revision: Union[str, None] = "0007_coder_prompt"
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

    researcher_content = (
        "Your role: Researcher.\n"
        "You explore codebases with read-only tools to find specific issues.\n"
        "Use grep to find patterns, read_file to confirm, done() to report.\n"
        "Be specific: include file paths, line numbers, and code snippets.\n"
        "Do NOT make any edits — only report what you find."
    )

    planner_content = (
        "Your role: Planner.\n"
        "You receive research findings and produce a concrete edit plan.\n"
        "For each file that needs changes, specify exactly what to do.\n"
        "Be specific enough that a developer can implement without ambiguity.\n"
        "Reply with a JSON plan. No prose outside the JSON."
    )

    implementer_content = (
        "Your role: Implementer.\n"
        "You receive a plan and execute it using edit_file and create_file.\n"
        "For each planned change: read the file, make the edit, verify it.\n"
        "Do NOT add changes beyond what the plan specifies.\n"
        "Do NOT skip any planned changes.\n"
        "Call done() when all changes are complete."
    )

    publisher_content = (
        "Your role: Publisher.\n"
        "You write clear PR titles and descriptions for code changes.\n"
        "Keep titles under 72 chars. Summarize changes, don't repeat diffs.\n"
        "Reply with a JSON object. No prose outside the JSON."
    )

    conn = op.get_bind()
    exists = conn.execute(
        sa.text("SELECT 1 FROM prompt_blocks WHERE key = 'researcher_role'")
    ).scalar()
    if exists:
        return

    op.bulk_insert(prompt_blocks, [
        {"id": str(uuid4()), "key": "researcher_role", "content": researcher_content, "category": "role", "agent_role": "researcher", "priority": 5, "is_active": True},
        {"id": str(uuid4()), "key": "planner_role", "content": planner_content, "category": "role", "agent_role": "planner", "priority": 5, "is_active": True},
        {"id": str(uuid4()), "key": "implementer_role", "content": implementer_content, "category": "role", "agent_role": "implementer", "priority": 5, "is_active": True},
        {"id": str(uuid4()), "key": "publisher_role", "content": publisher_content, "category": "role", "agent_role": "publisher", "priority": 5, "is_active": True},
    ])


def downgrade() -> None:
    op.execute("DELETE FROM prompt_blocks WHERE key IN ('researcher_role', 'planner_role', 'implementer_role', 'publisher_role')")
