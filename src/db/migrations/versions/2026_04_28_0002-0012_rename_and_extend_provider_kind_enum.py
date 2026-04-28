"""rename git_provider_kind to provider_kind and add jira value

Revision ID: 0012_rename_provider_kind
Revises: 0011_oauth_token_metadata
Create Date: 2026-04-28 00:00:00.000000

Two changes bundled together because the rename and value addition both touch
the same PostgreSQL enum type:

1. Add ``jira`` to the ``git_provider_kind`` enum so the Jira integration can
   be stored in ``project_repos.provider`` and ``user_oauth_credentials.provider``.

2. Rename the type from ``git_provider_kind`` to ``provider_kind`` to match the
   Python class rename (``GitProviderKind`` → ``ProviderKind``). The Python-level
   ``Enum(name=...)`` argument still references ``git_provider_kind`` until this
   migration runs, at which point the DB type is renamed to match.

``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction block on
PostgreSQL < 12, and even on PG 12+ the new value cannot be referenced within
the same transaction that added it. The ``autocommit_block()`` context manager
runs that single statement outside the migration transaction.

Downgrade note: PostgreSQL does not support ``DROP VALUE`` on enum types.
Rolling back will rename the type back to ``git_provider_kind`` but the
``jira`` value will remain. To fully remove it you would need to recreate
the type manually (see comments in downgrade()).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_rename_provider_kind"
down_revision: Union[str, None] = "0011_oauth_token_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD VALUE cannot run inside a transaction on PG < 12; use autocommit block.
    with op.get_context().autocommit_block():
        op.execute(sa.text("ALTER TYPE git_provider_kind ADD VALUE IF NOT EXISTS 'jira'"))

    # RENAME is safe inside a transaction.
    op.execute(sa.text("ALTER TYPE git_provider_kind RENAME TO provider_kind"))


def downgrade() -> None:
    # Rename the type back. The Python Enum(name="git_provider_kind") in the
    # SQLAlchemy models will match again after this.
    op.execute(sa.text("ALTER TYPE provider_kind RENAME TO git_provider_kind"))

    # NOTE: PostgreSQL does not support DROP VALUE on an enum type.
    # The 'jira' value will remain in git_provider_kind after downgrade.
    # To remove it manually, recreate the type:
    #
    #   CREATE TYPE git_provider_kind_new AS ENUM ('github');
    #   ALTER TABLE project_repos
    #       ALTER COLUMN provider TYPE git_provider_kind_new
    #       USING provider::text::git_provider_kind_new;
    #   ALTER TABLE user_oauth_credentials
    #       ALTER COLUMN provider TYPE git_provider_kind_new
    #       USING provider::text::git_provider_kind_new;
    #   DROP TYPE git_provider_kind;
    #   ALTER TYPE git_provider_kind_new RENAME TO git_provider_kind;
