"""Add Google Workspace MCP server configs and agent tool configs.

Revision ID: 0034_add_google_workspace_mcp
Revises: 0033_add_azure_provider_kind
Create Date: 2026-05-14 00:00:00.000000

Adds ``mcp_server_configs`` rows for Google's official Gmail and Calendar MCP
endpoints, and seeds ``agent_tool_configs`` so orchestrator agents can use
``mcp__google_gmail__*`` and ``mcp__google_calendar__*`` tools.

The ``google`` value already exists in the ``provider_kind`` enum from the
initial schema — no enum migration is needed.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034_add_google_workspace_mcp"
down_revision: Union[str, None] = "0033_add_azure_provider_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── mcp_server_configs ────────────────────────────────────────────────────
    op.execute(sa.text("""
        INSERT INTO mcp_server_configs
            (id, provider_name, transport_type, url_template, header_templates, extra_config, is_active, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'google_gmail',    'http', 'https://gmailmcp.googleapis.com/mcp/v1',    '{"Authorization": "Bearer {token}"}', '{}', true, now(), now()),
            (gen_random_uuid(), 'google_calendar', 'http', 'https://calendarmcp.googleapis.com/mcp/v1', '{"Authorization": "Bearer {token}"}', '{}', true, now(), now())
        ON CONFLICT (provider_name) DO NOTHING
    """))

    # ── agent_tool_configs ────────────────────────────────────────────────────
    # Orchestrator top-level (subagent_role IS NULL)
    op.execute(sa.text("""
        INSERT INTO agent_tool_configs
            (id, agent_name, subagent_role, tool_pattern, sort_order, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), 'orchestrator', NULL, pattern, idx, true, now(), now()
        FROM (VALUES
            (10, 'mcp__google_gmail__*'),
            (11, 'mcp__google_calendar__*')
        ) AS t(idx, pattern)
        WHERE NOT EXISTS (
            SELECT 1 FROM agent_tool_configs
            WHERE agent_name = 'orchestrator' AND subagent_role IS NULL AND tool_pattern = t.pattern
        )
    """))

    # Orchestrator / code-implementer
    op.execute(sa.text("""
        INSERT INTO agent_tool_configs
            (id, agent_name, subagent_role, tool_pattern, sort_order, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), 'orchestrator', 'code-implementer', pattern, idx, true, now(), now()
        FROM (VALUES
            (10, 'mcp__google_gmail__*'),
            (11, 'mcp__google_calendar__*')
        ) AS t(idx, pattern)
        WHERE NOT EXISTS (
            SELECT 1 FROM agent_tool_configs
            WHERE agent_name = 'orchestrator' AND subagent_role = 'code-implementer' AND tool_pattern = t.pattern
        )
    """))

    # Orchestrator / manager
    op.execute(sa.text("""
        INSERT INTO agent_tool_configs
            (id, agent_name, subagent_role, tool_pattern, sort_order, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), 'orchestrator', 'manager', pattern, idx, true, now(), now()
        FROM (VALUES
            (10, 'mcp__google_gmail__*'),
            (11, 'mcp__google_calendar__*')
        ) AS t(idx, pattern)
        WHERE NOT EXISTS (
            SELECT 1 FROM agent_tool_configs
            WHERE agent_name = 'orchestrator' AND subagent_role = 'manager' AND tool_pattern = t.pattern
        )
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DELETE FROM agent_tool_configs
        WHERE tool_pattern IN ('mcp__google_gmail__*', 'mcp__google_calendar__*')
    """))
    op.execute(sa.text("""
        DELETE FROM mcp_server_configs
        WHERE provider_name IN ('google_gmail', 'google_calendar')
    """))
