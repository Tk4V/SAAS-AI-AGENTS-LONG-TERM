"""add devops subagent and clyde_github in-process MCP

Revision ID: 0022_add_devops_subagent
Revises: 0021_user_agents
Create Date: 2026-05-07 00:01:00.000000

Adds the ``devops`` subagent that diagnoses GitHub Actions CI failures
and the matching ``clyde_github`` in-process MCP server config. The
in-process server is materialised in Python by
``OrchestratorAgent.build_in_process_mcp_servers`` — the row in
``mcp_server_configs`` exists only so ``subagent_tools`` and
``agent_subagent_mcps`` can reference it via foreign key.

Backfill: every existing default-orchestrator agent gets a fresh
``agent_subagents`` link to the new devops subagent and the matching
``agent_subagent_mcps`` rows so users with pre-existing agents see the
new sub-agent without creating a new agent.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_add_devops_subagent"
down_revision: Union[str, None] = "0021_user_agents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DEVOPS_SYSTEM_PROMPT = (
    "You are a DevOps engineer. The orchestrator delegates a CI-failure "
    "diagnosis-and-fix task to you. The user message contains the failing "
    "workflow run_id, repository (owner/name), and attempt number — extract "
    "them before you start.\n\n"
    "Workflow:\n"
    "1. Fetch the failed-job logs FIRST by calling "
    "mcp__clyde_github__get_failed_ci_logs with the run_id and repository "
    "from the parent's message (args: run_id=<int>, "
    "repo_full_name='owner/repo'). The tool returns a multi-section text "
    "with the tail of every failed job's log. Capture the actual error "
    "(stack trace, assertion message, lint diagnostic, type error, missing "
    "dependency, etc.) verbatim — this is the source of truth for "
    "everything that follows.\n"
    "2. Identify the root cause in the code. Use Read/Glob/Grep on the "
    "working tree to map the error location to the source file and line. "
    "Match the error message to the code precisely; do not jump to "
    "conclusions.\n"
    "3. Apply a minimal targeted fix. Edit only files directly implicated "
    "by the failure. Decision rules:\n"
    "  - test assertion failure → fix the code, not the test, unless the "
    "test expectation is clearly wrong relative to the original task.\n"
    "  - missing import / dependency → add it.\n"
    "  - type error → fix the type signatures.\n"
    "  - lint / format → apply the fix the linter expects.\n"
    "  - build / syntax error → resolve it.\n"
    "4. Return a short summary: which file:line you changed, what the "
    "root cause was (quote the log line), and what fix you applied. One "
    "paragraph.\n\n"
    "ABSOLUTE RULES — violating these is a critical failure:\n"
    "- Do NOT speculate about the failure. Step 1 is non-negotiable; "
    "every claim about the cause must come from a real log line returned "
    "by mcp__clyde_github__get_failed_ci_logs.\n"
    "- Do NOT touch files unrelated to the failure. No refactors, no "
    "cosmetic changes, no opportunistic cleanups.\n"
    "- Do NOT run git add/commit/checkout/push. The Publisher agent "
    "handles all git mutations after the parent's session ends.\n"
    "- Do NOT create pull requests or push branches.\n"
    "- If the logs tool returns a diagnostic string (e.g. 'failed to list "
    "jobs', 'no failed jobs in run', or a fetch error) or the right fix "
    "is not obvious, return a short explanation instead of guessing. "
    "NEEDS_HUMAN is an acceptable outcome; a wrong fix is not."
)

_DEVOPS_DESCRIPTION = (
    "DevOps engineer on Sonnet. Diagnoses GitHub Actions CI failures and "
    "applies minimal targeted code fixes. Use this sub-agent when the "
    "task description starts with the 'CI failure detected' marker — the "
    "marker carries the run_id, repository, and attempt number the "
    "sub-agent feeds into the in-process Clyde skill "
    "mcp__clyde_github__get_failed_ci_logs. The sub-agent fetches the "
    "failed-job logs through that skill, locates the root cause in the "
    "working tree, and edits the offending files. It does NOT commit, "
    "push, or open PRs; the Publisher handles that after the parent's "
    "session ends."
)


def upgrade() -> None:
    # ── Seed: devops subagent ────────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            INSERT INTO subagents
                (id, name, display_name, description, system_prompt, model,
                 sort_order, is_active, created_at, updated_at)
            VALUES
                (gen_random_uuid(), 'devops', 'DevOps Engineer',
                 :description, :system_prompt, 'sonnet',
                 50, true, now(), now())
            ON CONFLICT (name) DO NOTHING
            """
        ).bindparams(
            description=_DEVOPS_DESCRIPTION,
            system_prompt=_DEVOPS_SYSTEM_PROMPT,
        )
    )

    # ── Seed: subagent_system_tools — devops gets the standard code toolset ─
    op.execute(sa.text("""
        INSERT INTO subagent_system_tools
            (id, subagent_id, system_tool_id, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), s.id, t.id, true, now(), now()
        FROM subagents s
        JOIN system_tools t ON (s.name, t.name) IN (
            ('devops', 'read'),
            ('devops', 'edit'),
            ('devops', 'write'),
            ('devops', 'glob'),
            ('devops', 'grep'),
            ('devops', 'bash-git-diff'),
            ('devops', 'bash-pycompile')
        )
        ON CONFLICT (subagent_id, system_tool_id) DO NOTHING
    """))

    # ── Allow 'in_process' in mcp_server_configs.transport_type ─────────────
    # Original constraint (from 0018) limits values to ('http', 'sse'). The
    # in-process server is materialised in Python by
    # OrchestratorAgent.build_in_process_mcp_servers — its row exists only
    # so subagent_tools / agent_subagent_mcps can foreign-key it.
    op.execute(sa.text(
        "ALTER TABLE mcp_server_configs "
        "DROP CONSTRAINT ck_mcp_server_configs_ck_mcp_server_configs_transport_type"
    ))
    op.execute(sa.text(
        "ALTER TABLE mcp_server_configs "
        "ADD CONSTRAINT ck_mcp_server_configs_ck_mcp_server_configs_transport_type "
        "CHECK (transport_type IN ('http', 'sse', 'in_process'))"
    ))

    # ── Seed: clyde_github MCP server config ────────────────────────────────
    op.execute(sa.text("""
        INSERT INTO mcp_server_configs
            (id, provider_name, transport_type, url_template,
             header_templates, extra_config, is_active, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'clyde_github', 'in_process', '',
             '{}'::jsonb, '{}'::jsonb, true, now(), now())
        ON CONFLICT (provider_name) DO NOTHING
    """))

    # ── Seed: subagent_tools — devops uses clyde_github ─────────────────────
    op.execute(sa.text("""
        INSERT INTO subagent_tools
            (id, subagent_id, mcp_server_config_id, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), s.id, m.id, true, now(), now()
        FROM subagents s
        JOIN mcp_server_configs m ON (s.name, m.provider_name) IN (
            ('devops', 'clyde_github')
        )
        ON CONFLICT (subagent_id, mcp_server_config_id) DO NOTHING
    """))

    # ── Backfill: link devops to every existing default-orchestrator ────────
    op.execute(sa.text("""
        INSERT INTO agent_subagents
            (id, agent_id, subagent_id, sort_order, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), a.id, s.id, s.sort_order, true, now(), now()
        FROM agents a
        CROSS JOIN subagents s
        WHERE a.is_default IS true AND s.name = 'devops'
        ON CONFLICT (agent_id, subagent_id) DO NOTHING
    """))

    # ── Backfill: copy devops's MCP defaults onto each new agent_subagent ──
    op.execute(sa.text("""
        INSERT INTO agent_subagent_mcps
            (id, agent_subagent_id, mcp_server_config_id, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), asg.id, st.mcp_server_config_id, st.is_active, now(), now()
        FROM agent_subagents asg
        JOIN subagents s ON s.id = asg.subagent_id
        JOIN subagent_tools st ON st.subagent_id = s.id
        WHERE s.name = 'devops'
        ON CONFLICT (agent_subagent_id, mcp_server_config_id) DO NOTHING
    """))


def downgrade() -> None:
    # Remove backfill links first (FK ordering).
    op.execute(sa.text("""
        DELETE FROM agent_subagent_mcps asgm
        USING agent_subagents asg, subagents s
        WHERE asgm.agent_subagent_id = asg.id
          AND asg.subagent_id = s.id
          AND s.name = 'devops'
    """))
    op.execute(sa.text("""
        DELETE FROM agent_subagents asg
        USING subagents s
        WHERE asg.subagent_id = s.id AND s.name = 'devops'
    """))
    op.execute(sa.text("""
        DELETE FROM subagent_tools st
        USING subagents s, mcp_server_configs m
        WHERE st.subagent_id = s.id
          AND st.mcp_server_config_id = m.id
          AND s.name = 'devops'
          AND m.provider_name = 'clyde_github'
    """))
    op.execute(sa.text("""
        DELETE FROM subagent_system_tools sst
        USING subagents s
        WHERE sst.subagent_id = s.id AND s.name = 'devops'
    """))
    op.execute(sa.text(
        "DELETE FROM mcp_server_configs WHERE provider_name = 'clyde_github'"
    ))
    # Restore original ('http', 'sse') constraint from 0018.
    op.execute(sa.text(
        "ALTER TABLE mcp_server_configs "
        "DROP CONSTRAINT ck_mcp_server_configs_ck_mcp_server_configs_transport_type"
    ))
    op.execute(sa.text(
        "ALTER TABLE mcp_server_configs "
        "ADD CONSTRAINT ck_mcp_server_configs_ck_mcp_server_configs_transport_type "
        "CHECK (transport_type IN ('http', 'sse'))"
    ))
    op.execute(sa.text("DELETE FROM subagents WHERE name = 'devops'"))
