"""extend credential_kind and provider_kind enums

Revision ID: 0014_oauth_credential_kind
Revises: 0013_add_credentials
Create Date: 2026-04-29 00:00:01.000000

Two enum extensions bundled together because both are required for the new
OAuth flow that writes into the unified ``credentials`` table:

1. ``credential_kind`` gains the ``oauth`` value so the new credentials table
   can store OAuth tokens alongside bearer tokens.

2. ``provider_kind`` gains ``google`` and ``slack`` so the existing
   ``ProviderCatalog`` can register their OAuth configs without each new
   provider requiring its own migration.

``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction block on
PostgreSQL < 12, and the new value cannot be referenced inside the same
transaction even on PG 12+. The ``autocommit_block()`` keeps each ADD VALUE
in its own implicit transaction.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_oauth_credential_kind"
down_revision: Union[str, None] = "0013_add_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("ALTER TYPE credential_kind ADD VALUE IF NOT EXISTS 'oauth'")
        )
        op.execute(
            sa.text("ALTER TYPE provider_kind ADD VALUE IF NOT EXISTS 'google'")
        )
        op.execute(
            sa.text("ALTER TYPE provider_kind ADD VALUE IF NOT EXISTS 'slack'")
        )


def downgrade() -> None:
    # PostgreSQL does not support DROP VALUE on enum types. The added values
    # remain after downgrade. To remove them you would have to recreate each
    # enum and migrate the affected columns — see migration 0012 for the
    # template.
    pass
