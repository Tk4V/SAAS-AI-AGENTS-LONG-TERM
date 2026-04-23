"""add workspace instructions to prompt_blocks

Appends/prepends shared workspace (.clyde/) instructions to role-specific
prompt blocks so agents know to read and write workspace files during the
pipeline run.

Revision ID: 0006_workspace_instructions
Revises: 0005_prompt_blocks
Create Date: 2026-04-20 00:02:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0006_workspace_instructions"
down_revision: Union[str, None] = "0005_prompt_blocks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The text we append/prepend to each role's content block.
_TECH_LEAD_APPEND = (
    "\n\nAfter completing your analysis, write a detailed summary to "
    ".clyde/context.md in the repository using the create_file tool. "
    "Include all findings, relevant files with explanations, and open "
    "questions. Other agents will read this file to understand your full "
    "analysis without information loss."
)

_ARCHITECT_PREPEND = (
    "Before planning, read .clyde/context.md (written by Tech Lead) to "
    "see the full codebase analysis.\nAfter creating the plan, write it "
    "to .clyde/plan.md for other agents to reference.\n\n"
)

_SENIOR_DEV_PREPEND = (
    "Start by reading .clyde/context.md and .clyde/plan.md to understand "
    "the full context and plan.\n\n"
)

_CODE_REVIEWER_PREPEND = (
    "Read .clyde/plan.md to understand the intended changes before "
    "reviewing code.\n\n"
)


def upgrade() -> None:
    # Tech Lead: append workspace instructions at the end
    op.execute(
        "UPDATE prompt_blocks "
        "SET content = content || "
        f"$ws${_TECH_LEAD_APPEND}$ws$, "
        "updated_at = now() "
        "WHERE key = 'tech_lead_role'"
    )

    # Architect: prepend workspace instructions at the start
    op.execute(
        "UPDATE prompt_blocks "
        f"SET content = $ws${_ARCHITECT_PREPEND}$ws$ || content, "
        "updated_at = now() "
        "WHERE key = 'architect_role'"
    )

    # Senior Developer: prepend workspace instructions
    op.execute(
        "UPDATE prompt_blocks "
        f"SET content = $ws${_SENIOR_DEV_PREPEND}$ws$ || content, "
        "updated_at = now() "
        "WHERE key = 'senior_developer_role'"
    )

    # Code Reviewer: prepend workspace instructions
    op.execute(
        "UPDATE prompt_blocks "
        f"SET content = $ws${_CODE_REVIEWER_PREPEND}$ws$ || content, "
        "updated_at = now() "
        "WHERE key = 'code_reviewer_role'"
    )


def downgrade() -> None:
    # Strip the workspace instructions we added.
    # Tech Lead: remove the appended text
    op.execute(
        "UPDATE prompt_blocks "
        f"SET content = replace(content, $ws${_TECH_LEAD_APPEND}$ws$, ''), "
        "updated_at = now() "
        "WHERE key = 'tech_lead_role'"
    )

    # Architect: remove the prepended text
    op.execute(
        "UPDATE prompt_blocks "
        f"SET content = replace(content, $ws${_ARCHITECT_PREPEND}$ws$, ''), "
        "updated_at = now() "
        "WHERE key = 'architect_role'"
    )

    # Senior Developer: remove the prepended text
    op.execute(
        "UPDATE prompt_blocks "
        f"SET content = replace(content, $ws${_SENIOR_DEV_PREPEND}$ws$, ''), "
        "updated_at = now() "
        "WHERE key = 'senior_developer_role'"
    )

    # Code Reviewer: remove the prepended text
    op.execute(
        "UPDATE prompt_blocks "
        f"SET content = replace(content, $ws${_CODE_REVIEWER_PREPEND}$ws$, ''), "
        "updated_at = now() "
        "WHERE key = 'code_reviewer_role'"
    )
