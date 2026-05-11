"""Rename the ``repo-scanner`` subagent to ``code-auditor``.

Revision ID: 0031_code_auditor_rename
Revises: 0030_subagents_memory_access
Create Date: 2026-05-12 00:03:00.000000

Why
---
The historical name ``repo-scanner`` overlapped with ``manager`` (both
have Jira create access) and didn't communicate the agent's actual
contract — *evidence-based audit that emits Jira tickets per finding*.
``code-auditor`` reflects that intent more accurately and pairs cleanly
with ``code-explorer`` (read-only discovery) and ``code-implementer``
(file edits) in the catalog.

Updates three places:
  * ``subagents.name`` / ``subagents.display_name`` — the canonical row.
  * ``subagents.system_prompt`` — opens with the new identity so the
    LLM doesn't speak of itself as a "repo auditor" any more; the
    workflow body is unchanged.
  * ``agent_tool_configs.subagent_role`` — legacy seed data from the
    pre-link era; harmless to leave but renamed for hygiene so a future
    grep doesn't find ``repo-scanner`` orphan strings.

All other references (``subagent_system_tools``, ``agent_subagent_mcps``,
``subagent_tools``) join on ``subagents.id``, so the FK relationships
follow the rename automatically.

Idempotent — the rename UPDATE is gated on the row still being named
``repo-scanner``; the prompt rewrite is gated on the prompt still
containing the old opener so re-runs are no-ops.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031_code_auditor_rename"
down_revision: Union[str, Sequence[str], None] = "0030_subagents_memory_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_PROMPT = (
    "You are a code auditor. The orchestrator delegates evidence-based audit "
    "tasks to you — your job is to find concrete issues in the working tree "
    "and emit one trackable item per finding (Jira issue or GitHub issue, "
    "depending on what the parent specifies). You do NOT edit code; the "
    "orchestrator chains code-implementer for that.\n\n"
    "--- DEFAULT WORKFLOW: JIRA ---\n"
    "1. Use Read/Glob/Grep to scan the working tree for what the parent asked. "
    "Capture concrete evidence (file:line + snippet) for every finding — never "
    "invent.\n"
    "2. Before creating tickets, list available Jira projects with "
    "jira_get_all_projects and pick the one the parent named. If no exact "
    "name/key match, STOP and report 'no matching project' to the parent — "
    "do NOT guess a near-miss project.\n"
    "3. For each finding, create one Jira issue via jira_create_issue. Include "
    "the file:line evidence in the description. Capture the returned issue key.\n"
    "4. After creation, verify each new key with jira_get_issue (expect found). "
    "If a creation errored, record the error verbatim.\n"
    "5. Report: project key used, list of (finding, issue key) pairs, list of "
    "failures. Never claim a ticket exists unless step 4 confirmed it.\n\n"
    "--- ALTERNATIVE WORKFLOW: GITHUB ISSUES ---\n"
    "If the parent specifies GitHub instead of Jira:\n"
    "1. Confirm the target repo from the parent message (owner/repo format).\n"
    "2. For each finding, create one GitHub Issue via mcp__github__create_issue. "
    "Include the file:line evidence in the body. Capture the returned issue "
    "number.\n"
    "3. Verify each issue was created (non-null issue number in response). "
    "Record any failures verbatim.\n"
    "4. Report: repo used, list of (finding, issue number) pairs, list of "
    "failures.\n\n"
    "Hard rules (apply to both workflows): no file edits, no fabricated "
    "findings or issue keys/numbers, no asking the user."
)


_OLD_PROMPT_OPENER = "You are a repo auditor."


def upgrade() -> None:
    # 1. Rename the subagents row itself + rewrite the system prompt.
    #    The prompt update is gated on the old opener so re-runs are no-ops
    #    and so a hand-edited prompt isn't accidentally clobbered.
    op.execute(sa.text("""
        UPDATE subagents
        SET name = 'code-auditor',
            display_name = 'Code Auditor',
            updated_at = now()
        WHERE name = 'repo-scanner'
    """))
    op.execute(
        sa.text("""
            UPDATE subagents
            SET system_prompt = :new_prompt,
                updated_at = now()
            WHERE name = 'code-auditor'
              AND system_prompt LIKE :old_marker
        """).bindparams(
            new_prompt=_NEW_PROMPT,
            old_marker=f"{_OLD_PROMPT_OPENER}%",
        )
    )

    # 2. Update legacy agent_tool_configs rows that hardcoded the role
    #    string. Runtime code calls get_effective_tool_patterns with
    #    subagent_role=None for the orchestrator, so these rows are
    #    effectively dormant — but we rename them anyway so a string
    #    search across the codebase comes back clean.
    op.execute(sa.text("""
        UPDATE agent_tool_configs
        SET subagent_role = 'code-auditor',
            updated_at = now()
        WHERE subagent_role = 'repo-scanner'
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        UPDATE subagents
        SET name = 'repo-scanner',
            display_name = 'Repo Scanner',
            updated_at = now()
        WHERE name = 'code-auditor'
    """))
    op.execute(sa.text("""
        UPDATE agent_tool_configs
        SET subagent_role = 'repo-scanner',
            updated_at = now()
        WHERE subagent_role = 'code-auditor'
    """))
    # Prompt downgrade is intentionally not attempted — bringing back the
    # exact previous text from a duplicated string would just rot when the
    # earlier migrations evolve. If a true rollback is needed, restore from
    # backup or re-apply the seed migration's text directly.
