"""Append an escalation-to-parent block to every subagent system_prompt.

Revision ID: 0026_subagent_escalation_prompt
Revises: 0025_add_task_messages
Create Date: 2026-05-11 00:02:00.000000

Why
---
With CA-113 the orchestrator gained the ``ask_user`` tool so it can pause
the pipeline and pull missing context from the human. Subagents do not get
that tool — only the orchestrator talks to the user, so the team speaks
with one voice. But existing subagent prompts predate this design and
either explicitly forbid asking back ("do not ask clarifying questions
back — make the best judgement call") or stay silent on the topic, which
in practice causes the subagent to fabricate or refuse instead of
escalating.

This migration appends a standard escalation block to every existing
subagent's system_prompt so they know to return a structured request to
the orchestrator rather than guess. The block is idempotent — gated on a
marker string so re-running the migration is a no-op.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_subagent_escalation_prompt"
down_revision: Union[str, Sequence[str], None] = "0025_add_task_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MARKER = "<!-- ca-113-escalation -->"

_ESCALATION_BLOCK = f"""

{_MARKER}
ESCALATION TO PARENT — when you lack information you cannot derive yourself.

You DO NOT have access to a channel for asking the user directly. Only the
orchestrator talks to the user; you talk to the orchestrator. When the task
cannot be completed honestly because of missing information (credentials,
target system, ambiguous scope, missing file, unknown business rule),
return a short structured request to the parent agent INSTEAD of guessing
or refusing silently.

Format your escalation as the final line(s) of your reply, like this:

    NEEDS_INPUT_FROM_USER:
    - missing: <what you need, one item per bullet>
    - reason:  <why you need it, one sentence>
    - shape:   <what kind of answer would let you proceed>

The orchestrator will decide whether to relay the question to the user via
its ``ask_user`` tool, pick a safe default, or abort the task. Never
fabricate data to fill the gap, never silently choose for the user when
the choice is destructive.
"""


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE subagents
            SET system_prompt = system_prompt || :block,
                updated_at = now()
            WHERE position(:marker IN system_prompt) = 0
            """
        ).bindparams(block=_ESCALATION_BLOCK, marker=_MARKER)
    )


def downgrade() -> None:
    # Strip the appended block back out by trimming everything from the
    # marker to end-of-string. Uses left() with the marker offset so we
    # do not lose anything that came before it.
    op.execute(
        sa.text(
            """
            UPDATE subagents
            SET system_prompt = rtrim(left(system_prompt, position(:marker IN system_prompt) - 1)),
                updated_at = now()
            WHERE position(:marker IN system_prompt) > 0
            """
        ).bindparams(marker=_MARKER)
    )
