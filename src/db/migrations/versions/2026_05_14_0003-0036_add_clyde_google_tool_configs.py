"""Add clyde_google in-process MCP tool configs.

Revision ID: 0036_clyde_google_tool_configs
Revises: 0035_widen_credentials_preview
Create Date: 2026-05-14 00:00:00.000000

Seeds agent_tool_configs rows for mcp__clyde_google__* so the orchestrator
agent and its subagent roles can use the in-process Google Workspace tools
(search_emails, create_draft, create_calendar_event, create_meet_meeting, etc.).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036_clyde_google_tool_configs"
down_revision: Union[str, None] = "0035_widen_credentials_preview"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for subagent_role, role_sql in [
        ("NULL", "NULL"),
        ("'code-implementer'", "'code-implementer'"),
        ("'manager'", "'manager'"),
    ]:
        op.execute(sa.text(f"""
            INSERT INTO agent_tool_configs
                (id, agent_name, subagent_role, tool_pattern, sort_order, is_active, created_at, updated_at)
            SELECT gen_random_uuid(), 'orchestrator', {role_sql}, 'mcp__clyde_google__*', 20, true, now(), now()
            WHERE NOT EXISTS (
                SELECT 1 FROM agent_tool_configs
                WHERE agent_name = 'orchestrator'
                  AND subagent_role IS NOT DISTINCT FROM {role_sql}
                  AND tool_pattern = 'mcp__clyde_google__*'
            )
        """))


def downgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM agent_tool_configs WHERE tool_pattern = 'mcp__clyde_google__*'"
    ))
