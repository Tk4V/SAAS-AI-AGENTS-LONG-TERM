"""Normalise in-process MCP tool patterns in agent_tool_configs.

Revision ID: 0037_fix_inprocess_mcp_patterns
Revises: 0036_clyde_google_tool_configs
Create Date: 2026-05-14 00:00:00.000000

In-process MCP servers for Azure and Google were registered under
``clyde_azure`` / ``clyde_google`` server names, producing tool prefixes
``mcp__clyde_azure__*`` and ``mcp__clyde_google__*``.  The server names
have been shortened to ``azure`` and ``google`` to match the pattern used
by every other MCP integration (github, jira, slack, aws).  This migration:

1. Renames stale ``mcp__clyde_azure__*`` → ``mcp__azure__*``
2. Collapses ``mcp__google_gmail__*``, ``mcp__google_calendar__*``, and
   ``mcp__clyde_google__*`` → ``mcp__google__*``
3. Deduplicates any resulting duplicate (agent_name, subagent_role) pairs
4. Ensures the full set of role variants exists for both providers
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037_fix_inprocess_mcp_patterns"
down_revision: Union[str, None] = "0036_clyde_google_tool_configs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_AZURE_PATTERNS = ("mcp__clyde_azure__*",)
_OLD_GOOGLE_PATTERNS = (
    "mcp__clyde_google__*",
    "mcp__google_gmail__*",
    "mcp__google_calendar__*",
)


def upgrade() -> None:
    # ── 1. Rename old azure pattern ───────────────────────────────────────────
    op.execute(sa.text("""
        UPDATE agent_tool_configs
           SET tool_pattern = 'mcp__azure__*', updated_at = now()
         WHERE tool_pattern IN ('mcp__clyde_azure__*')
    """))

    # ── 2. Collapse old google patterns ──────────────────────────────────────
    op.execute(sa.text("""
        UPDATE agent_tool_configs
           SET tool_pattern = 'mcp__google__*', updated_at = now()
         WHERE tool_pattern IN (
             'mcp__clyde_google__*',
             'mcp__google_gmail__*',
             'mcp__google_calendar__*'
         )
    """))

    # ── 3. Deduplicate: keep one row per (agent_name, subagent_role) pair ────
    for pattern in ("mcp__azure__*", "mcp__google__*"):
        op.execute(sa.text(f"""
            DELETE FROM agent_tool_configs
            WHERE tool_pattern = '{pattern}'
              AND id NOT IN (
                SELECT DISTINCT ON (agent_name, subagent_role) id
                FROM agent_tool_configs
                WHERE tool_pattern = '{pattern}'
                ORDER BY agent_name, subagent_role, created_at
              )
        """))

    # ── 4. Ensure full role coverage (NULL, code-implementer, manager) ───────
    for tool_pattern in ("mcp__azure__*", "mcp__google__*"):
        for role_sql in ("NULL", "'code-implementer'", "'manager'"):
            op.execute(sa.text(f"""
                INSERT INTO agent_tool_configs
                    (id, agent_name, subagent_role, tool_pattern, sort_order, is_active, created_at, updated_at)
                SELECT gen_random_uuid(), 'orchestrator', {role_sql}, '{tool_pattern}', 20, true, now(), now()
                WHERE NOT EXISTS (
                    SELECT 1 FROM agent_tool_configs
                    WHERE agent_name = 'orchestrator'
                      AND subagent_role IS NOT DISTINCT FROM {role_sql}
                      AND tool_pattern = '{tool_pattern}'
                )
            """))


def downgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM agent_tool_configs WHERE tool_pattern IN ('mcp__azure__*', 'mcp__google__*')"
    ))
