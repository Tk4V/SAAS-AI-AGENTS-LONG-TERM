"""Add ``tasks.sdk_session_id`` column so chat sessions can survive an app restart.

Revision ID: 0032_add_sdk_session_id
Revises: 0031_code_auditor_rename
Create Date: 2026-05-12 00:04:00.000000

Why
---
The Claude Agent SDK persists conversation transcripts as JSONL files and
exposes a ``resume=<session_id>`` option that reloads the full history
into a new ``ClaudeSDKClient`` instance. The SDK stores those transcripts
through a pluggable ``SessionStore`` adapter — we point the adapter at
S3 in a follow-up commit so the JSONL bytes survive a container destroy
and any pod can pick the session up.

This migration adds the column the orchestrator writes ``session_id``
into when it first opens the SDK client; on subsequent app starts the
restart-resume hook reads it back and passes it as ``resume`` so the
agent continues exactly where it left off (same context, same prefix
cache, same agent persona).

Nullable because pre-CA-113 tasks never had a session; making it NOT
NULL would require a backfill against the SDK store and a default that
doesn't have a sensible value. Empty stays empty.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_add_sdk_session_id"
down_revision: Union[str, Sequence[str], None] = "0031_code_auditor_rename"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE tasks "
        "ADD COLUMN IF NOT EXISTS sdk_session_id UUID NULL"
    ))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE tasks DROP COLUMN IF EXISTS sdk_session_id"))
