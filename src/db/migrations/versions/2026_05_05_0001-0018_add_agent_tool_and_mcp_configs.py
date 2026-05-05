"""Add agent_tool_configs and mcp_server_configs tables with seed data.

Revision ID: 0018_agent_tool_mcp_configs
Revises: 0017_merge_heads
Create Date: 2026-05-05 00:00:00.000000

Moves hardcoded SDK_ALLOWED_TOOLS and MCP factory functions into DB rows so
they can be updated without a code deploy. Seed rows mirror the values that
were previously hardcoded in OrchestratorAgent and PublisherAgent.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_agent_tool_mcp_configs"
down_revision: Union[str, None] = "0017_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── mcp_server_configs ────────────────────────────────────────────────────
    op.create_table(
        "mcp_server_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column("transport_type", sa.String(16), nullable=False),
        sa.Column("url_template", sa.String(512), nullable=False),
        sa.Column(
            "header_templates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "extra_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_mcp_server_configs"),
        sa.UniqueConstraint("provider_name", name="uq_mcp_server_configs_provider_name"),
        sa.CheckConstraint("transport_type IN ('http', 'sse')", name="ck_mcp_server_configs_transport_type"),
    )

    # ── agent_tool_configs ────────────────────────────────────────────────────
    op.create_table(
        "agent_tool_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(64), nullable=False),
        sa.Column("subagent_role", sa.String(64), nullable=True),
        sa.Column("tool_pattern", sa.String(256), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_tool_configs"),
    )
    op.create_index("ix_agent_tool_configs_agent_name", "agent_tool_configs", ["agent_name"])
    op.create_index("ix_agent_tool_configs_agent_subagent", "agent_tool_configs", ["agent_name", "subagent_role"])

    # ── Seed: mcp_server_configs ──────────────────────────────────────────────
    op.execute(sa.text("""
        INSERT INTO mcp_server_configs
            (id, provider_name, transport_type, url_template, header_templates, extra_config, is_active, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'github', 'sse',  'https://api.githubcopilot.com/mcp/',       '{"Authorization": "Bearer {token}"}', '{}', true, now(), now()),
            (gen_random_uuid(), 'jira',   'http', 'https://mcp.atlassian.com/v1/mcp/authv2', '{"Authorization": "Bearer {token}"}', '{}', true, now(), now()),
            (gen_random_uuid(), 'slack',  'http', 'https://mcp.slack.com/mcp',               '{"Authorization": "Bearer {token}"}', '{}', true, now(), now()),
            (gen_random_uuid(), 'aws',    'http', 'http://127.0.0.1:8000/api/v1/mcp/aws',    '{"X-AWS-Credentials": "{token}"}',   '{}', true, now(), now())
        ON CONFLICT (provider_name) DO NOTHING
    """))

    # ── Seed: agent_tool_configs (MCP integration tools only) ────────────────
    # Built-in SDK tools (Read, Edit, Write, Glob, Grep, Agent, Bash variants)
    # live in code as SYSTEM_TOOLS ClassVar per agent — never seeded here.

    # Orchestrator top-level MCP tools (subagent_role IS NULL)
    op.execute(sa.text("""
        INSERT INTO agent_tool_configs
            (id, agent_name, subagent_role, tool_pattern, sort_order, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), 'orchestrator', NULL, pattern, idx, true, now(), now()
        FROM (VALUES
            (0, 'mcp__github__*'),
            (1, 'mcp__jira__*'),
            (2, 'mcp__slack__*'),
            (3, 'mcp__aws__*')
        ) AS t(idx, pattern)
        WHERE NOT EXISTS (
            SELECT 1 FROM agent_tool_configs WHERE agent_name = 'orchestrator' AND subagent_role IS NULL
        )
    """))

    # Orchestrator / code-implementer MCP tools
    op.execute(sa.text("""
        INSERT INTO agent_tool_configs
            (id, agent_name, subagent_role, tool_pattern, sort_order, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), 'orchestrator', 'code-implementer', pattern, idx, true, now(), now()
        FROM (VALUES
            (0, 'mcp__github__*'),
            (1, 'mcp__jira__*'),
            (2, 'mcp__slack__*'),
            (3, 'mcp__aws__*')
        ) AS t(idx, pattern)
        WHERE NOT EXISTS (
            SELECT 1 FROM agent_tool_configs WHERE agent_name = 'orchestrator' AND subagent_role = 'code-implementer'
        )
    """))

    # code-explorer and test-runner have no MCP tools — no rows needed.

    # Orchestrator / manager MCP tools
    op.execute(sa.text("""
        INSERT INTO agent_tool_configs
            (id, agent_name, subagent_role, tool_pattern, sort_order, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), 'orchestrator', 'manager', pattern, idx, true, now(), now()
        FROM (VALUES
            (0, 'mcp__jira__*')
        ) AS t(idx, pattern)
        WHERE NOT EXISTS (
            SELECT 1 FROM agent_tool_configs WHERE agent_name = 'orchestrator' AND subagent_role = 'manager'
        )
    """))

    # Orchestrator / repo-scanner MCP tools
    op.execute(sa.text("""
        INSERT INTO agent_tool_configs
            (id, agent_name, subagent_role, tool_pattern, sort_order, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), 'orchestrator', 'repo-scanner', pattern, idx, true, now(), now()
        FROM (VALUES
            (0, 'mcp__jira__*')
        ) AS t(idx, pattern)
        WHERE NOT EXISTS (
            SELECT 1 FROM agent_tool_configs WHERE agent_name = 'orchestrator' AND subagent_role = 'repo-scanner'
        )
    """))

    # Publisher top-level tools (subagent_role IS NULL)
    op.execute(sa.text("""
        INSERT INTO agent_tool_configs
            (id, agent_name, subagent_role, tool_pattern, sort_order, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), 'publisher', NULL, pattern, idx, true, now(), now()
        FROM (VALUES
            (0, 'mcp__github__*')
        ) AS t(idx, pattern)
        WHERE NOT EXISTS (
            SELECT 1 FROM agent_tool_configs WHERE agent_name = 'publisher' AND subagent_role IS NULL
        )
    """))


def downgrade() -> None:
    op.drop_index("ix_agent_tool_configs_agent_subagent", table_name="agent_tool_configs")
    op.drop_index("ix_agent_tool_configs_agent_name", table_name="agent_tool_configs")
    op.drop_table("agent_tool_configs")
    op.drop_table("mcp_server_configs")
