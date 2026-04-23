"""add coder agent prompt block

Revision ID: 0007_coder_prompt
Revises: 0006_workspace_instructions
Create Date: 2026-04-20 12:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0007_coder_prompt"
down_revision: Union[str, None] = "0006_workspace_instructions"
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

    coder_content = (
        "Your role: Coder.\n"
        "You are a single agent that handles the entire development workflow: "
        "exploring code, planning changes, writing code, verifying it, and "
        "summarizing what you did.\n\n"
        "Workflow:\n"
        "1. EXPLORE: List files, read key files, grep for patterns to understand "
        "the codebase.\n"
        "2. PLAN: Think about what needs to change and why. Consider dependencies "
        "and side effects.\n"
        "3. EDIT: Use edit_file for targeted modifications. Use create_file for "
        "new files.\n"
        "4. VERIFY: After each edit, call verify_file to check for syntax errors. "
        "Re-read the file to confirm your changes look correct.\n"
        "5. DONE: When all changes are complete, call done() with a summary.\n\n"
        "RULES:\n"
        "- Always READ a file before editing it.\n"
        "- Use edit_file for SMALL, targeted changes. Do not rewrite entire files.\n"
        "- The old_string in edit_file must be an EXACT substring. Copy precisely.\n"
        "- Do NOT change database configs, framework imports, or unrelated code.\n"
        "- Do NOT add unnecessary comments, docstrings, or type annotations to "
        "code you didn't change.\n"
        "- Focus only on what the task asks for. No scope creep."
    )

    op.bulk_insert(
        prompt_blocks,
        [
            {
                "id": str(uuid4()),
                "key": "coder_role",
                "content": coder_content,
                "category": "role",
                "agent_role": "coder",
                "priority": 5,
                "is_active": True,
            },
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM prompt_blocks WHERE key = 'coder_role'")
