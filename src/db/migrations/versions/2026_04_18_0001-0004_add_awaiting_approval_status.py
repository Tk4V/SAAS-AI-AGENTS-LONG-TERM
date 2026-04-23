"""add awaiting_approval status

Revision ID: 0004_awaiting_approval
Revises: 0003_add_users
Create Date: 2026-04-18 00:01:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0004_awaiting_approval"
down_revision: Union[str, None] = "0002_user_oauth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'awaiting_approval'")


def downgrade() -> None:
    # Postgres does not support removing enum values. This is a no-op.
    pass
