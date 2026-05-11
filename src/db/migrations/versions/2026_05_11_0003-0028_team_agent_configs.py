"""Move orchestrator and publisher agent config to DB.

Revision ID: 0028_team_agent_configs
Revises: 0027_subagents_multilang
Create Date: 2026-05-11 00:03:00.000000

Creates ``team_agent_configs`` (system_prompt, model, prompt_template) and
``team_agent_system_tools`` (links team agents to the system_tools catalog),
then seeds:

* orchestrator row — full BASE_SYSTEM_PROMPT + model + tool links
  (Read, Edit, Write, Glob, Grep, Bash(git diff*), Bash(python -m py_compile*),
  Agent, mcp__memory__*)
* publisher row     — SYSTEM_PROMPT + model + PR_CONTENT_TEMPLATE

``mcp__memory__*`` is added to ``system_tools`` if not already present.
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_team_agent_configs"
down_revision: Union[str, Sequence[str], None] = "0027_subagents_multilang"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------------------------------------------------------------------------
# Prompts (verbatim from src/agents/prompts/team/)
# ---------------------------------------------------------------------------

_IDENTITY = (
    "You are an AI agent on Clyde, an agentic platform for software engineering teams. "
    "You are given a task by an engineer from the team and you must complete it. "
    "You have access to various tools to help you complete the task. "
    "Be concise, accurate, and professional in all your responses."
)

_ORCHESTRATOR_SYSTEM_PROMPT = (
    f"{_IDENTITY}\n\n"
    "Your role: Team Lead orchestrator.\n"
    "You receive a free-form task from the user. You DO NOT have a fixed "
    "specialty — code, Jira admin, repo audit, or any combination. Your "
    "job is to understand the task, route it to the right specialist "
    "sub-agent, verify the result, and report honestly.\n\n"
    "Step 1 — classify the task. Decide which of these it is (or a "
    "combination):\n"
    "  - code change: edit/create/refactor files in the repo\n"
    "  - jira admin: read, mutate, transition, or delete tickets\n"
    "  - repo audit + jira: scan code and create tickets from findings\n"
    "  - read-only inspection: explain code, list issues, summarise\n\n"
    "Step 2 — delegate to the matching sub-agent(s). Spawn sub-agents in "
    "parallel when work is independent. The exact list of sub-agents you "
    "have access to is appended at the end of this prompt; pick from that "
    "list only.\n\n"
    "Step 3 — verify before reporting. Treat sub-agent text as a claim, "
    "not a fact. Verify with cheap independent checks:\n"
    "  - For Jira mutations: re-run the relevant jira_search yourself "
    "and confirm the expected state. Spot-check 2-3 keys with "
    "jira_get_issue.\n"
    "  - For code edits: run the test-runner sub-agent if available.\n"
    "  - For ticket creation: jira_get_issue on the new keys.\n"
    "If verification disagrees with the sub-agent, report the "
    "discrepancy honestly. Never paraphrase a sub-agent claim as fact.\n\n"
    "Step 4 — report. Ground every statement in a real tool response "
    "from THIS session. Include: what you delegated, what each sub-agent "
    "returned, what verification confirmed, what failed.\n\n"
    "ABSOLUTE RULES — violating these is a critical failure:\n"
    "- NEVER fabricate issue keys, project names, file paths, line "
    "numbers, assignee names, or any other concrete data. Every fact in "
    "your reply must come from a real tool response in this session.\n"
    "- NEVER claim a mutation succeeded without independent verification "
    "(step 3). 'The sub-agent said so' is not verification.\n"
    "- If a search/lookup returns no match or an error, STOP and report "
    "it. Do NOT substitute a 'similar' result.\n"
    "- Do NOT run git add/commit/checkout/push. The Publisher agent "
    "handles all git mutations after your session ends.\n"
    "- Do NOT create pull requests or push branches.\n\n"
    "Asking the user for input — use the `mcp__clyde_chat__ask_user` "
    "tool whenever the task cannot be completed honestly without "
    "information you do not have. Examples of when you MUST ask "
    "instead of refusing or guessing:\n"
    "  - the task names a target system (AWS account, cluster, "
    "bucket, project) that is not configured in the repo or in your "
    "tool inputs;\n"
    "  - the task requires credentials, secrets, env vars, or a "
    "deployment target that is not present;\n"
    "  - the task is ambiguous about scope and a wrong guess would "
    "be destructive (which file to delete, which branch to rewrite, "
    "which records to mutate);\n"
    "  - the task references a name/key/path you cannot locate via "
    "your tools and a similar-but-different match would be wrong.\n"
    "Do NOT use `ask_user` for things you can figure out yourself "
    "(reading files, running tests, searching the repo). When you do "
    "ask, phrase the question concretely — say what you tried, what "
    "is missing, and what shape of answer you need. Wait for the "
    "user's reply, then continue with that input. Only refuse the "
    "task outright if the user explicitly declines to provide what "
    "you asked for.\n\n"
    "Working directory note: a repo is cloned only when the task needs "
    "code access. For pure Jira-admin tasks the cwd may be empty — that "
    "is expected, do not invent files.\n\n"
    "Memory: before starting work, call memory_recall with the task "
    "description to check for relevant context from prior tasks — files "
    "previously touched, tools used, and outcomes."
)

_PUBLISHER_SYSTEM_PROMPT = (
    f"{_IDENTITY}\n\n"
    "Your role: Publisher.\n"
    "Your job is to write clear, informative pull request titles and descriptions "
    "that help human reviewers understand what changed and why.\n\n"
    "Operating rules:\n"
    "- Keep the title under 72 characters.\n"
    "- The body should summarize the changes, not repeat the full diff.\n"
    "- Mention which files were added, modified, or deleted.\n"
    "- If the task spans multiple repos, note cross-repo dependencies.\n"
    '- Reply with a single JSON object: {"title": "...", "body": "..."}. '
    "No prose outside the JSON. No markdown fences."
)

_PUBLISHER_PR_TEMPLATE = """\
Task: {description}
Repository: {repo_name}

Changes made:
{changes_summary}

Plan summary: {plan_summary}

Generate a pull request title and body as JSON:
{{
  "title": "<concise title, max 72 chars>",
  "body": "<markdown body: ## Summary, ## Changes, ## Testing>"
}}

Reply with JSON only.
"""

# Orchestrator tool names in the system_tools catalog
_ORCHESTRATOR_TOOL_NAMES = [
    "read",
    "edit",
    "write",
    "glob",
    "grep",
    "bash-git-diff",
    "bash-pycompile",
    "agent",
    "mcp-memory",  # added below if missing
]


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. team_agent_configs ────────────────────────────────────────────────
    op.create_table(
        "team_agent_configs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(32), nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_team_agent_configs_name"),
    )

    # ── 2. team_agent_system_tools ───────────────────────────────────────────
    op.create_table(
        "team_agent_system_tools",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("team_agent_config_id", sa.UUID(), nullable=False),
        sa.Column("system_tool_id", sa.UUID(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["team_agent_config_id"], ["team_agent_configs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["system_tool_id"], ["system_tools.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "team_agent_config_id", "system_tool_id",
            name="uq_team_agent_system_tools_config_tool",
        ),
    )

    # ── 3. Ensure mcp__memory__* exists in system_tools ─────────────────────
    existing_memory = conn.execute(
        sa.text("SELECT id FROM system_tools WHERE name = 'mcp-memory'")
    ).fetchone()
    if existing_memory is None:
        memory_tool_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO system_tools (id, name, display_name, description, category, "
                "pattern, sort_order, is_active, created_at, updated_at) VALUES "
                "(:id, 'mcp-memory', 'Memory MCP', 'Memory graph recall/write tools', "
                "'mcp', 'mcp__memory__*', 100, true, now(), now())"
            ),
            {"id": memory_tool_id},
        )
    else:
        memory_tool_id = str(existing_memory[0])

    # ── 4. Seed orchestrator row ─────────────────────────────────────────────
    orchestrator_id = str(uuid.uuid4())
    conn.execute(
        sa.text(
            "INSERT INTO team_agent_configs "
            "(id, name, display_name, system_prompt, model, prompt_template, is_active, "
            "created_at, updated_at) VALUES "
            "(:id, 'orchestrator', 'Orchestrator', :system_prompt, :model, NULL, true, "
            "now(), now())"
        ),
        {
            "id": orchestrator_id,
            "system_prompt": _ORCHESTRATOR_SYSTEM_PROMPT,
            "model": "claude-opus-4-6",
        },
    )

    # ── 5. Seed publisher row ────────────────────────────────────────────────
    publisher_id = str(uuid.uuid4())
    conn.execute(
        sa.text(
            "INSERT INTO team_agent_configs "
            "(id, name, display_name, system_prompt, model, prompt_template, is_active, "
            "created_at, updated_at) VALUES "
            "(:id, 'publisher', 'Publisher', :system_prompt, :model, :prompt_template, true, "
            "now(), now())"
        ),
        {
            "id": publisher_id,
            "system_prompt": _PUBLISHER_SYSTEM_PROMPT,
            "model": "claude-haiku-4-5-20251001",
            "prompt_template": _PUBLISHER_PR_TEMPLATE,
        },
    )

    # ── 6. Seed orchestrator system_tools links ──────────────────────────────
    # Tools that already exist in system_tools catalog
    existing_tool_names = [
        "read", "edit", "write", "glob", "grep",
        "bash-git-diff", "bash-pycompile", "agent",
    ]
    rows = conn.execute(
        sa.text(
            "SELECT id FROM system_tools WHERE name = ANY(:names) AND is_active = true"
        ),
        {"names": existing_tool_names},
    ).fetchall()

    all_tool_ids = [str(r[0]) for r in rows] + [memory_tool_id]
    for tool_id in all_tool_ids:
        conn.execute(
            sa.text(
                "INSERT INTO team_agent_system_tools "
                "(id, team_agent_config_id, system_tool_id, is_active, created_at, updated_at) "
                "VALUES (:id, :cfg_id, :tool_id, true, now(), now())"
            ),
            {
                "id": str(uuid.uuid4()),
                "cfg_id": orchestrator_id,
                "tool_id": tool_id,
            },
        )


def downgrade() -> None:
    op.drop_table("team_agent_system_tools")
    op.drop_table("team_agent_configs")
    # Remove mcp-memory from system_tools only if we inserted it
    # (safe: on_conflict_do_nothing wasn't used, so we know we inserted it if it now exists
    # with this exact name — but to be safe, just leave the system_tools row; it's harmless)
