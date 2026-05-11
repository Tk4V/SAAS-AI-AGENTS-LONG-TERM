"""Add task_approvals table for human-in-the-loop tool permission flow.

Revision ID: 0024_add_task_approvals
Revises: 0023_merge_heads
Create Date: 2026-05-08 00:01:00.000000

Tables created
--------------
* ``task_approvals`` — one row per tool-use permission request raised by the
  orchestrator's ``can_use_tool`` callback. The pipeline pauses until the
  owning user resolves it via POST /tasks/{id}/approvals/{approval_id}/resolve.

Enum added
----------
* ``approval_status`` — (pending, approved, denied)
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_add_task_approvals"
down_revision: Union[str, Sequence[str], None] = "0023_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL throughout to avoid SQLAlchemy's Enum auto-create firing
    # a second CREATE TYPE via the table-create event hook.
    op.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE approval_status AS ENUM ('pending', 'approved', 'denied');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS task_approvals (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            user_id     BIGINT      NOT NULL,
            task_id     UUID        NOT NULL,
            tool_name   VARCHAR     NOT NULL,
            tool_input  JSONB       NOT NULL DEFAULT '{}',
            status      approval_status NOT NULL DEFAULT 'pending',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_task_approvals PRIMARY KEY (id),
            CONSTRAINT fk_task_approvals_task_id_tasks
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        )
    """))

    op.create_index("ix_task_approvals_user_id", "task_approvals", ["user_id"])
    op.create_index("ix_task_approvals_task_id", "task_approvals", ["task_id"])
    op.create_index("ix_task_approvals_status", "task_approvals", ["status"])


def downgrade() -> None:
    op.drop_index("ix_task_approvals_status", table_name="task_approvals")
    op.drop_index("ix_task_approvals_task_id", table_name="task_approvals")
    op.drop_index("ix_task_approvals_user_id", table_name="task_approvals")
    op.drop_table("task_approvals")
    op.execute("DROP TYPE approval_status")
