"""add aws value to provider_kind enum

Revision ID: 0016_add_aws_provider_kind
Revises: 0015_drop_user_oauth_credentials
Create Date: 2026-05-01 00:00:00.000000

Add ``aws`` to the ``provider_kind`` PostgreSQL enum to support the AWS MCP
Preview server integration. The Python ``ProviderKind`` enum has already been
extended; this migration keeps the DB type in sync.

``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction block, so the
statement is executed via ``autocommit_block()``.

Downgrade note: PostgreSQL does not support ``DROP VALUE`` on an enum type.
Rolling back this migration is a no-op at the DB level — the ``aws`` value
will remain in the type but will no longer be referenced by application code.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_add_aws_provider_kind"
down_revision: Union[str, None] = "0015_drop_user_oauth_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text("ALTER TYPE provider_kind ADD VALUE IF NOT EXISTS 'aws'"))


def downgrade() -> None:
    # PostgreSQL does not support DROP VALUE on an enum type.
    # The 'aws' value will remain after downgrade.
    pass
