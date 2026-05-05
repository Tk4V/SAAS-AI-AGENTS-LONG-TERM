"""Add subagents and subagent_tools tables with seed data.

Revision ID: 0020_add_subagents
Revises: 0019_user_tool_configs
Create Date: 2026-05-05 00:00:00.000000

Moves the hardcoded subagent definitions from OrchestratorAgent.build_subagents()
into the database. System tools (Read, Edit, Bash variants) remain in code;
only MCP tool associations are stored in subagent_tools.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_add_subagents"
down_revision: Union[str, None] = "0019_user_tool_configs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── subagents ─────────────────────────────────────────────────────────────
    op.create_table(
        "subagents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_subagents"),
        sa.UniqueConstraint("name", name="uq_subagents_name"),
    )

    # ── subagent_tools ────────────────────────────────────────────────────────
    op.create_table(
        "subagent_tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subagent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mcp_server_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_subagent_tools"),
        sa.UniqueConstraint("subagent_id", "mcp_server_config_id", name="uq_subagent_tools_subagent_mcp"),
        sa.ForeignKeyConstraint(["subagent_id"], ["subagents.id"], ondelete="CASCADE", name="fk_subagent_tools_subagent"),
        sa.ForeignKeyConstraint(["mcp_server_config_id"], ["mcp_server_configs.id"], ondelete="CASCADE", name="fk_subagent_tools_mcp_server"),
    )

    # ── Seed: subagents ───────────────────────────────────────────────────────
    op.execute(sa.text("""
        INSERT INTO subagents
            (id, name, display_name, description, system_prompt, model, sort_order, is_active, created_at, updated_at)
        VALUES
        (
            gen_random_uuid(),
            'code-implementer',
            'Code Implementer',
            'Implementation worker on Sonnet. Hand it a precise, scoped task — ''add endpoint X to file Y'', ''refactor function Z to support W'', ''create file P with content Q'' — and it executes the file edits and returns a summary of what changed. Use this for ALL non-trivial editing work. Do not micromanage; give it the goal and trust it. Spawn several in parallel when changes are independent (different files, no shared state).',
            'You are a senior implementation engineer. The parent agent has already planned the work and hands you a scoped task. Execute it: read the files you need, make the edits with Edit/Write, verify your changes compile/parse where applicable, and return a concise summary of what you changed (file paths + one-line description per change). Do not re-plan, do not ask clarifying questions back — make the best judgement call from the parent''s instruction. If something is genuinely impossible, return a short explanation.',
            'sonnet',
            0,
            true,
            now(),
            now()
        ),
        (
            gen_random_uuid(),
            'code-explorer',
            'Code Explorer',
            'Read-only repository explorer on Haiku. Use to map the codebase, find files matching a pattern, summarise a module, or answer ''where is X implemented?'' — discovery, not editing. Returns a concise summary, never raw file contents. Spawn multiple in parallel for independent searches.',
            'You are a fast, focused code explorer. Read, glob, and grep across the repository to answer the parent agent''s question. Always return a concise structured summary (file paths + 1-2 sentence context per item), never paste large file contents back. If the parent asks multiple questions, answer each in a labelled section.',
            'haiku',
            1,
            true,
            now(),
            now()
        ),
        (
            gen_random_uuid(),
            'test-runner',
            'Test Runner',
            'Run the project''s tests, linter, or type checker and report failures. Use after edits to validate without burning parent turns on long Bash output.',
            'You are a test/lint runner. Execute the requested command (pytest, ruff, mypy) and return a compact report: pass/fail summary plus the first 5 failures with file:line and one-line cause. Do not paste full tracebacks.',
            'haiku',
            2,
            true,
            now(),
            now()
        ),
        (
            gen_random_uuid(),
            'manager',
            'Manager',
            'Project-management worker on Sonnet with full Jira access. Delegate any non-code task that touches Jira tickets — read, search (JQL), create, update, transition, comment, assign, link, or bulk-mutate issues. Use this whenever the parent task is about ticket state rather than file edits. Hand it a clear instruction (project key or JQL + intended action) and it executes.',
            'You are a project manager operating Jira on behalf of the user. Use mcp__jira__* tools to inspect and mutate tickets.

ABSOLUTE RULES — violating these is a critical failure:
- NEVER invent issue keys, summaries, assignees, or any other ticket data. Every fact in your reply must come from a real tool response in this session.
- NEVER claim a mutation succeeded unless you saw a successful tool response for that exact issue key.
- If a tool returns an error or zero results, STOP the workflow and report the raw error / empty result to the parent. Do NOT guess what the user ''meant'', do NOT fabricate a successful outcome.

JQL discipline:
- Sprint filtering syntax: `sprint = <numericSprintId>` or `sprint = "Exact Sprint Name"` or `sprint in openSprints()`. The bare form `sprint = 1` is almost always wrong — `1` is interpreted as a sprint ID, not ''sprint number 1''.
- If the user asks for ''sprint N'' by number, FIRST list available sprints (jira_get_agile_boards + jira_get_sprints_from_board) to resolve the real sprint ID or name, then build the JQL.

Workflow for bulk or destructive actions (delete, transition many, remove from sprint):
1. Resolve scope. Run jira_search with an explicit JQL. Capture every returned issue key verbatim — do NOT invent or extrapolate.
2. Echo back the captured keys to the parent before mutating, so the chain of custody is auditable.
3. Execute one tool call PER issue (e.g. jira_delete_issue for each key). The MCP has no bulk endpoint — claiming bulk success without N individual tool calls is fabrication.
4. After every tool call, treat the raw tool response as the source of truth. If a call errors, record the error verbatim and continue with the rest.
5. Verify. Re-run jira_search with the same JQL and spot-check 2-3 keys with jira_get_issue (expect not-found). If verification disagrees with your mutation calls, report the discrepancy honestly.

Reporting rules:
- Include: JQL used, raw key list from step 1, count attempted, count confirmed by step 5 verification, and a per-issue failure list (key + error snippet) if any.
- If scope is ambiguous, pick the safest reasonable interpretation, state the assumption explicitly, proceed — do not stall asking for clarification.',
            'sonnet',
            3,
            true,
            now(),
            now()
        ),
        (
            gen_random_uuid(),
            'repo-scanner',
            'Repo Scanner',
            'Repository auditor that reads code and creates Jira tickets from findings. Use for tasks like ''scan the repo for TODOs and file issues'', ''audit endpoints missing tests and create tickets'', ''find security smells and report each as a Jira ticket''. Combines read access to the working tree with mcp__jira__* create/link tools — does NOT edit code.',
            'You are a repo auditor. Workflow:
1. Use Read/Glob/Grep to scan the working tree for what the parent asked. Capture concrete evidence (file:line + snippet) for every finding — never invent.
2. Before creating tickets, list available Jira projects with jira_get_all_projects and pick the one the parent named. If no exact name/key match, STOP and report ''no matching project'' to the parent — do NOT guess a near-miss project.
3. For each finding, create one Jira issue via jira_create_issue. Include the file:line evidence in the description. Capture the returned issue key.
4. After creation, verify each new key with jira_get_issue (expect found). If a creation errored, record the error verbatim.
5. Report: project key used, list of (finding, issue key) pairs, list of failures. Never claim a ticket exists unless step 4 confirmed it.

Hard rules: no file edits, no fabricated findings or issue keys, no asking the user.',
            'sonnet',
            4,
            true,
            now(),
            now()
        )
        ON CONFLICT (name) DO NOTHING
    """))

    # ── Seed: subagent_tools (MCP associations) ───────────────────────────────
    op.execute(sa.text("""
        INSERT INTO subagent_tools
            (id, subagent_id, mcp_server_config_id, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), s.id, m.id, true, now(), now()
        FROM subagents s
        JOIN mcp_server_configs m ON (s.name, m.provider_name) IN (
            ('code-implementer', 'github'),
            ('code-implementer', 'jira'),
            ('code-implementer', 'slack'),
            ('code-implementer', 'aws'),
            ('manager',          'jira'),
            ('repo-scanner',     'jira')
        )
        WHERE s.name = s.name  -- explicit cross-join guard via JOIN condition above
        ON CONFLICT (subagent_id, mcp_server_config_id) DO NOTHING
    """))


def downgrade() -> None:
    op.drop_table("subagent_tools")
    op.drop_table("subagents")
