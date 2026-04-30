"""drop legacy user_oauth_credentials table

Revision ID: 0015_drop_user_oauth_credentials
Revises: 0014_oauth_credential_kind
Create Date: 2026-04-29 00:00:02.000000

Step 3b of the credentials unification: the new ``credentials`` table now
owns OAuth credentials (``kind=oauth``) and every call-site has been moved
to ``CredentialResolver`` / ``OAuthTokenProvider``. The legacy
``user_oauth_credentials`` table is no longer read or written by any code
path so we drop it.

There is no data migration here because the database had no production
rows in ``user_oauth_credentials`` at the time this migration was written.
If you find yourself running this against a database that does, copy the
rows into ``credentials`` first via a separate maintenance script — Alembic
cannot do the decrypt/reshape/re-encrypt the conversion requires.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_drop_user_oauth_credentials"
down_revision: Union[str, None] = "0014_oauth_credential_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(
        "ix_user_oauth_credentials_user_id",
        table_name="user_oauth_credentials",
    )
    op.drop_table("user_oauth_credentials")


def downgrade() -> None:
    # Recreating the legacy table is intentionally not supported. Migrations
    # 0002 and 0011 contain the exact CREATE/ALTER SQL if a manual rollback
    # is ever needed.
    raise NotImplementedError(
        "Cannot recreate user_oauth_credentials automatically. "
        "Replay migrations 0002 and 0011 manually if you need it back."
    )
