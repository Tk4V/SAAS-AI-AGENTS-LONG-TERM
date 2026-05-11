"""Add awaiting_user_message and publishing values to task_status enum.

Revision ID: 0029_add_chat_session_statuses
Revises: 0028_team_agent_configs
Create Date: 2026-05-12 00:01:00.000000

Why
---
CA-113 turns the task pipeline into a long-lived chat session. After the
orchestrator finishes a turn, the task does not immediately complete —
it transitions through ``publishing`` (commit/push to GitHub) and then
sits in ``awaiting_user_message`` until the user sends a follow-up or the
session times out. These two values are added to the existing
``task_status`` Postgres enum.

Downgrade is intentionally lossy: Postgres has no DROP VALUE for enums,
so the downgrade leaves the values in the enum but updates any rows that
hold them to a safe fallback (``failed``) before the application starts
expecting the old enum shape.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029_add_chat_session_statuses"
down_revision: Union[str, Sequence[str], None] = "0028_team_agent_configs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block in
    # older Postgres releases. Modern Alembic + Postgres 13+ accept it
    # inside the migration transaction, which is what we run against.
    op.execute(sa.text(
        "ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'awaiting_user_message'"
    ))
    op.execute(sa.text(
        "ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'publishing'"
    ))


def downgrade() -> None:
    # Bring any rows holding the new values back to a recognised state so
    # the application can keep running with the older enum shape.
    op.execute(sa.text("""
        UPDATE tasks
        SET status = 'failed',
            error_message = COALESCE(error_message, '') ||
                ' (downgraded from awaiting_user_message/publishing)'
        WHERE status IN ('awaiting_user_message', 'publishing')
    """))
    # Postgres has no DROP VALUE; values remain in the type but are unused.
