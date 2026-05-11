"""Add task_messages chat history table and approval user_response column.

Revision ID: 0025_add_task_messages
Revises: 0024_add_task_approvals
Create Date: 2026-05-11 00:01:00.000000

Tables created
--------------
* ``task_messages`` — append-only chat history between user and agents.
  Bootstrapped by the UI via GET /tasks/{id}/messages, then extended in
  real time over the bidirectional WebSocket /ws/tasks/{id}/chat.

Columns added
-------------
* ``task_approvals.user_response`` (JSONB, nullable) — payload the user
  attached when resolving an approval, e.g. free-form text answer or
  structured JSON for an ``ask_user`` tool.

Enums added
-----------
* ``message_role`` — (user, agent, system)
* ``message_kind`` — (chat, approval_request, approval_response, status, error)
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_add_task_messages"
down_revision: Union[str, Sequence[str], None] = "0024_add_task_approvals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE message_role AS ENUM ('user', 'agent', 'system');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE message_kind AS ENUM (
                'chat', 'approval_request', 'approval_response', 'status', 'error'
            );
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS task_messages (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            user_id     BIGINT      NOT NULL,
            task_id     UUID        NOT NULL,
            role        message_role NOT NULL,
            kind        message_kind NOT NULL DEFAULT 'chat',
            content     TEXT        NOT NULL DEFAULT '',
            author      VARCHAR     NULL,
            meta        JSONB       NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_task_messages PRIMARY KEY (id),
            CONSTRAINT fk_task_messages_task_id_tasks
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        )
    """))

    op.create_index("ix_task_messages_user_id", "task_messages", ["user_id"])
    op.create_index("ix_task_messages_task_id", "task_messages", ["task_id"])
    op.create_index("ix_task_messages_kind", "task_messages", ["kind"])
    op.create_index(
        "ix_task_messages_task_id_created_at",
        "task_messages",
        ["task_id", "created_at"],
    )

    op.execute(sa.text("""
        ALTER TABLE task_approvals
        ADD COLUMN IF NOT EXISTS user_response JSONB NULL
    """))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE task_approvals DROP COLUMN IF EXISTS user_response"))
    op.drop_index("ix_task_messages_task_id_created_at", table_name="task_messages")
    op.drop_index("ix_task_messages_kind", table_name="task_messages")
    op.drop_index("ix_task_messages_task_id", table_name="task_messages")
    op.drop_index("ix_task_messages_user_id", table_name="task_messages")
    op.drop_table("task_messages")
    op.execute("DROP TYPE IF EXISTS message_kind")
    op.execute("DROP TYPE IF EXISTS message_role")
