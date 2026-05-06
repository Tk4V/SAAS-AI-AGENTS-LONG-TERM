"""User-scoped agents, system tools catalog, per-link MCP overrides.

Revision ID: 0021_user_agents
Revises: 0020_add_subagents
Create Date: 2026-05-06 00:00:00.000000

Lifts the orchestrator config out of code and into per-user rows so each
user can compose multiple orchestrators by picking subagents from the admin
catalog. Also lifts the previously hardcoded system tools (Read, Edit, Bash
variants, etc.) into a DB catalog so admin can attach them to any subagent
without a Python edit.

Tables created
--------------
* ``system_tools`` — admin catalog of built-in SDK tool patterns
  (e.g. Read, Edit, Bash(git diff*), Agent).
* ``subagent_system_tools`` — admin-defined link between a subagent and the
  system tools it has access to. Replaces the ``_system_tools`` dict that
  used to live in ``OrchestratorAgent.build_subagents``.
* ``agents`` — per-user orchestrator instance. Each user can have many.
  ``system_prompt`` and ``model`` are nullable; runtime falls back to the
  in-code defaults when NULL (kept around for prompt/model A/B testing
  later, but the user-facing API does not expose these fields yet).
* ``agent_subagents`` — which subagents a given agent uses.
* ``agent_subagent_mcps`` — per-link MCP override. Lets the user pick
  exactly which MCP integrations a subagent can use inside one specific
  agent. Populated from ``subagent_tools`` defaults at link-creation time.

Tables altered
--------------
* ``tasks`` — gains ``agent_id`` (NOT NULL after backfill).

Backfill
--------
Every existing user (by ``DISTINCT user_id`` in ``tasks``) gets one default
agent ``default-orchestrator`` linked to all currently active subagents,
and each link is filled with the matching subagent's MCP defaults. Then
every existing ``tasks`` row is pointed at its owner's new default agent
before the column is made NOT NULL.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_user_agents"
down_revision: Union[str, None] = "0020_add_subagents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── system_tools ──────────────────────────────────────────────────────────
    op.create_table(
        "system_tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(64), nullable=False, server_default="general"),
        sa.Column("pattern", sa.String(256), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_system_tools"),
        sa.UniqueConstraint("name", name="uq_system_tools_name"),
    )

    # ── subagent_system_tools ────────────────────────────────────────────────
    op.create_table(
        "subagent_system_tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subagent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("system_tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_subagent_system_tools"),
        sa.UniqueConstraint("subagent_id", "system_tool_id", name="uq_subagent_system_tools_subagent_tool"),
        sa.ForeignKeyConstraint(["subagent_id"], ["subagents.id"], ondelete="CASCADE", name="fk_subagent_system_tools_subagent_id_subagents"),
        sa.ForeignKeyConstraint(["system_tool_id"], ["system_tools.id"], ondelete="CASCADE", name="fk_subagent_system_tools_system_tool_id_system_tools"),
    )

    # ── agents (per-user orchestrators) ──────────────────────────────────────
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(2000), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("model", sa.String(32), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agents"),
        sa.UniqueConstraint("user_id", "name", name="uq_agents_user_id_name"),
    )
    op.create_index("ix_agents_user_id", "agents", ["user_id"])
    # Postgres considers NULL ≠ NULL, so we restrict the partial index to
    # rows where is_default IS true — this guarantees one default per user.
    op.execute(
        "CREATE UNIQUE INDEX ix_agents_user_id_default "
        "ON agents (user_id) WHERE is_default IS true"
    )

    # ── agent_subagents ──────────────────────────────────────────────────────
    op.create_table(
        "agent_subagents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subagent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_subagents"),
        sa.UniqueConstraint("agent_id", "subagent_id", name="uq_agent_subagents_agent_id_subagent_id"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE", name="fk_agent_subagents_agent_id_agents"),
        sa.ForeignKeyConstraint(["subagent_id"], ["subagents.id"], ondelete="CASCADE", name="fk_agent_subagents_subagent_id_subagents"),
    )
    op.create_index("ix_agent_subagents_agent_id", "agent_subagents", ["agent_id"])

    # ── agent_subagent_mcps ──────────────────────────────────────────────────
    op.create_table(
        "agent_subagent_mcps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_subagent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mcp_server_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_subagent_mcps"),
        sa.UniqueConstraint("agent_subagent_id", "mcp_server_config_id", name="uq_agent_subagent_mcps_link_mcp"),
        sa.ForeignKeyConstraint(["agent_subagent_id"], ["agent_subagents.id"], ondelete="CASCADE", name="fk_agent_subagent_mcps_agent_subagent_id_agent_subagents"),
        sa.ForeignKeyConstraint(["mcp_server_config_id"], ["mcp_server_configs.id"], ondelete="CASCADE", name="fk_agent_subagent_mcps_mcp_server_config_id_mcp_server_configs"),
    )
    op.create_index("ix_agent_subagent_mcps_link", "agent_subagent_mcps", ["agent_subagent_id"])

    # ── tasks.agent_id (nullable for backfill, NOT NULL at the end) ──────────
    op.add_column(
        "tasks",
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_tasks_agent_id_agents",
        "tasks",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_tasks_agent_id", "tasks", ["agent_id"])

    # ── Seed: system_tools (the previously hardcoded set) ────────────────────
    op.execute(sa.text("""
        INSERT INTO system_tools
            (id, name, display_name, description, category, pattern, sort_order, is_active, created_at, updated_at)
        VALUES
        (gen_random_uuid(), 'read',         'Read files',           'Read file contents from the working tree.',                    'filesystem', 'Read',                          0,  true, now(), now()),
        (gen_random_uuid(), 'edit',         'Edit files',           'Edit existing files with exact-match string replacement.',     'filesystem', 'Edit',                          1,  true, now(), now()),
        (gen_random_uuid(), 'write',        'Write files',          'Create or overwrite files in the working tree.',               'filesystem', 'Write',                         2,  true, now(), now()),
        (gen_random_uuid(), 'glob',         'Glob',                 'Find files by glob pattern.',                                  'filesystem', 'Glob',                          3,  true, now(), now()),
        (gen_random_uuid(), 'grep',         'Grep',                 'Search file contents with ripgrep.',                           'filesystem', 'Grep',                          4,  true, now(), now()),
        (gen_random_uuid(), 'bash-git-diff','Bash: git diff',       'Run read-only git diff commands.',                             'shell',      'Bash(git diff*)',               10, true, now(), now()),
        (gen_random_uuid(), 'bash-pycompile','Bash: python compile','Validate Python syntax via py_compile.',                       'shell',      'Bash(python -m py_compile*)',   11, true, now(), now()),
        (gen_random_uuid(), 'bash-pytest', 'Bash: pytest',          'Run pytest test suites.',                                      'shell',      'Bash(pytest*)',                 12, true, now(), now()),
        (gen_random_uuid(), 'bash-ruff',   'Bash: ruff',            'Run ruff linter / formatter.',                                 'shell',      'Bash(ruff*)',                   13, true, now(), now()),
        (gen_random_uuid(), 'bash-mypy',   'Bash: mypy',            'Run mypy type checker.',                                       'shell',      'Bash(mypy*)',                   14, true, now(), now()),
        (gen_random_uuid(), 'agent',       'Agent',                 'Delegate work to a registered subagent.',                      'orchestration','Agent',                       20, true, now(), now())
        ON CONFLICT (name) DO NOTHING
    """))

    # ── Seed: subagent_system_tools (re-create the old hardcoded mapping) ────
    # Mirrors what _system_tools used to be inside OrchestratorAgent.
    op.execute(sa.text("""
        INSERT INTO subagent_system_tools
            (id, subagent_id, system_tool_id, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), s.id, t.id, true, now(), now()
        FROM subagents s
        JOIN system_tools t ON (s.name, t.name) IN (
            ('code-implementer', 'read'),
            ('code-implementer', 'edit'),
            ('code-implementer', 'write'),
            ('code-implementer', 'glob'),
            ('code-implementer', 'grep'),
            ('code-implementer', 'bash-git-diff'),
            ('code-implementer', 'bash-pycompile'),

            ('code-explorer',    'read'),
            ('code-explorer',    'glob'),
            ('code-explorer',    'grep'),

            ('test-runner',      'bash-pytest'),
            ('test-runner',      'bash-ruff'),
            ('test-runner',      'bash-mypy'),
            ('test-runner',      'bash-pycompile'),

            ('repo-scanner',     'read'),
            ('repo-scanner',     'glob'),
            ('repo-scanner',     'grep')
        )
        ON CONFLICT (subagent_id, system_tool_id) DO NOTHING
    """))

    # ── Backfill: one default agent per existing user (by tasks.user_id) ─────
    op.execute(sa.text("""
        INSERT INTO agents
            (id, user_id, name, display_name, description, system_prompt, model,
             is_default, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), t.user_id, 'default-orchestrator', 'Default Orchestrator',
               'Auto-created default orchestrator. Bundles all available subagents.',
               NULL, NULL, true, true, now(), now()
        FROM (SELECT DISTINCT user_id FROM tasks) t
        ON CONFLICT (user_id, name) DO NOTHING
    """))

    # ── Backfill: link every active subagent to each new default agent ───────
    op.execute(sa.text("""
        INSERT INTO agent_subagents
            (id, agent_id, subagent_id, sort_order, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), a.id, s.id, s.sort_order, true, now(), now()
        FROM agents a
        CROSS JOIN subagents s
        WHERE s.is_active IS true
        ON CONFLICT (agent_id, subagent_id) DO NOTHING
    """))

    # ── Backfill: copy admin MCP defaults from subagent_tools per link ───────
    op.execute(sa.text("""
        INSERT INTO agent_subagent_mcps
            (id, agent_subagent_id, mcp_server_config_id, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), asg.id, st.mcp_server_config_id, st.is_active, now(), now()
        FROM agent_subagents asg
        JOIN subagent_tools st ON st.subagent_id = asg.subagent_id
        ON CONFLICT (agent_subagent_id, mcp_server_config_id) DO NOTHING
    """))

    # ── Backfill: point existing tasks at their owner's default agent ────────
    op.execute(sa.text("""
        UPDATE tasks t
        SET agent_id = a.id
        FROM agents a
        WHERE a.user_id = t.user_id AND a.is_default IS true AND t.agent_id IS NULL
    """))

    # Now that every row has agent_id, lock it down.
    op.alter_column("tasks", "agent_id", nullable=False)


def downgrade() -> None:
    op.drop_index("ix_tasks_agent_id", table_name="tasks")
    op.drop_constraint("fk_tasks_agent_id_agents", "tasks", type_="foreignkey")
    op.drop_column("tasks", "agent_id")

    op.drop_index("ix_agent_subagent_mcps_link", table_name="agent_subagent_mcps")
    op.drop_table("agent_subagent_mcps")

    op.drop_index("ix_agent_subagents_agent_id", table_name="agent_subagents")
    op.drop_table("agent_subagents")

    op.execute("DROP INDEX IF EXISTS ix_agents_user_id_default")
    op.drop_index("ix_agents_user_id", table_name="agents")
    op.drop_table("agents")

    op.drop_table("subagent_system_tools")
    op.drop_table("system_tools")
