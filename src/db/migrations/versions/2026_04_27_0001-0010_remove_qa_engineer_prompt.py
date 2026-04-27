"""remove qa_engineer prompt block

Revision ID: 0010_remove_qa_engineer_prompt
Revises: 0009_developer_prompt
Create Date: 2026-04-27 00:01:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0010_remove_qa_engineer_prompt"
down_revision: Union[str, None] = "0009_developer_prompt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM prompt_blocks WHERE key = 'qa_engineer_role'")


def downgrade() -> None:
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
        "Your role: QA Engineer.\n"
        "Before running tests you have read-only tools to understand the test "
        "structure: read_file, grep, list_files.\n\n"
        "Use them to:\n"
        "1. Find test files (grep for \"test_\" or list tests/ directories).\n"
        "2. Read conftest.py and key test files to understand fixtures and setup.\n"
        "3. Understand what the tests cover relative to the changed files.\n"
        "4. When done, call done() with a JSON summary of your findings.\n\n"
        "The done() summary must be a JSON object with this schema:\n"
        "{\n"
        '  "test_files": ["<path to test file>", "..."],\n'
        '  "conftest_found": true/false,\n'
        '  "fixtures": ["<fixture name>", "..."],\n'
        '  "coverage_notes": "<brief notes on what the tests cover>"\n'
        "}\n\n"
        "Focus on understanding test structure, not running tests."
    )

    op.bulk_insert(prompt_blocks, [{
        "id": str(uuid4()), "key": "qa_engineer_role", "content": content,
        "category": "role", "agent_role": "qa_engineer", "priority": 5, "is_active": True,
    }])
