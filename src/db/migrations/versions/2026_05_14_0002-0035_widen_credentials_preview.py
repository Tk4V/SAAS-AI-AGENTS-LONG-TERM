"""Widen credentials.preview from VARCHAR(64) to VARCHAR(512).

Revision ID: 0035_widen_credentials_preview
Revises: 0034_add_google_workspace_mcp
Create Date: 2026-05-14 00:00:00.000000

Google OAuth tokens include multiple long scope URLs in the preview string,
which exceeded the previous 64-character limit.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035_widen_credentials_preview"
down_revision: Union[str, None] = "0034_add_google_workspace_mcp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "credentials",
        "preview",
        existing_type=sa.String(64),
        type_=sa.String(512),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "credentials",
        "preview",
        existing_type=sa.String(512),
        type_=sa.String(64),
        existing_nullable=False,
    )
