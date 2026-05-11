"""Grant every active subagent access to the in-process memory MCP.

Revision ID: 0030_subagents_memory_access
Revises: 0029_add_chat_session_statuses
Create Date: 2026-05-12 00:02:00.000000

Why
---
The memory MCP server (``mcp__memory__*``) is mounted in-process by the
orchestrator and exposes ``memory_recall`` / ``memory_write`` tools that
let agents reuse cross-task knowledge. Until now only the orchestrator
itself had it in its allowed-tools list — every spawned subagent had to
relay context manually through the orchestrator's prompt, which is both
slower and lossy.

This migration links the existing ``mcp-memory`` row in the
``system_tools`` catalog (seeded by 0028) to every currently active
subagent via ``subagent_system_tools``. Because the link is in the DB
the admin UI can disable memory for a specific subagent later without
needing a code change.

Idempotent — gated on ``ON CONFLICT DO NOTHING`` against the existing
unique key (subagent_id, system_tool_id).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_subagents_memory_access"
down_revision: Union[str, Sequence[str], None] = "0029_add_chat_session_statuses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # If 0028 didn't run on this DB for some reason, nothing to link to —
    # the SELECT will return zero rows, the INSERT becomes a no-op, no
    # error. Same shape on a fresh local DB.
    op.execute(sa.text("""
        INSERT INTO subagent_system_tools
            (id, subagent_id, system_tool_id, is_active, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            s.id,
            t.id,
            true,
            now(),
            now()
        FROM subagents s
        CROSS JOIN system_tools t
        WHERE s.is_active = true
          AND t.name = 'mcp-memory'
          AND t.is_active = true
        ON CONFLICT (subagent_id, system_tool_id) DO NOTHING
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DELETE FROM subagent_system_tools sst
        USING system_tools t
        WHERE sst.system_tool_id = t.id
          AND t.name = 'mcp-memory'
    """))
