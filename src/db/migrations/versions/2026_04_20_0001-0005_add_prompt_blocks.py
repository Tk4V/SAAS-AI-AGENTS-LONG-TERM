"""add prompt_blocks table with seed data

Moves all hardcoded system prompts from prompts.py files into the
prompt_blocks table. PromptAssembler now loads blocks from DB instead
of assembling them from Python constants.

Revision ID: 0005_prompt_blocks
Revises: 0004_awaiting_approval
Create Date: 2026-04-20 00:01:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0005_prompt_blocks"
down_revision: Union[str, None] = "0004_awaiting_approval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    table_exists = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'prompt_blocks')"
        )
    ).scalar()
    if table_exists:
        return

    prompt_blocks = op.create_table(
        "prompt_blocks",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("content", sa.String, nullable=False),
        sa.Column("category", sa.String(32), nullable=False, server_default="shared"),
        sa.Column("agent_role", sa.String(64), nullable=True),
        sa.Column("priority", sa.Integer, nullable=False, server_default="50"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("key", name="uq_prompt_blocks_key"),
    )

    op.create_index("ix_prompt_blocks_agent_role", "prompt_blocks", ["agent_role"])

    # ------------------------------------------------------------------
    # Seed data: shared blocks that every agent inherits
    # ------------------------------------------------------------------

    identity_content = (
        "You are an AI agent on Clyde, a virtual development team "
        "that ships code across multiple git repositories."
    )

    safety_content = (
        "SAFETY RULES (always enforced):\n"
        "- Never modify database configurations unless explicitly asked.\n"
        "- Never replace frameworks or libraries.\n"
        "- Never remove existing code that isn't mentioned in the task.\n"
        "- Never expose secrets, tokens, or credentials in output.\n"
        "- Never execute destructive operations without confirmation."
    )

    tool_rules_content = (
        "TOOL USAGE RULES:\n"
        "- Always READ a file before editing it.\n"
        "- Use edit_file for small, targeted changes \u2014 not full rewrites.\n"
        "- The old_string in edit_file must be an exact match. Copy precisely.\n"
        "- Call done() when finished. Do not just stop responding.\n"
        "- Call verify_file after editing to check for errors."
    )

    # ------------------------------------------------------------------
    # Seed data: role-specific blocks (one per agent)
    # ------------------------------------------------------------------

    tech_lead_content = (
        "Your role: Tech Lead.\n"
        "You have read-only tools to explore the codebase: read_file, grep, list_files.\n"
        "Use them to build a thorough understanding of the repositories before "
        "producing your context summary.\n\n"
        "Workflow:\n"
        "1. Start by listing files in each repository to understand the structure.\n"
        "2. Read the most relevant files for the task (entry points, configs, models).\n"
        "3. Grep for patterns related to the task (class names, endpoints, etc.).\n"
        "4. When you have enough context, call done() with a JSON summary.\n\n"
        "The done() summary must be a JSON object with exactly this schema:\n"
        "{\n"
        '  "summary": "<two or three sentences describing what these repos do together>",\n'
        '  "repos": [\n'
        "    {\n"
        '      "name": "<repo name>",\n'
        '      "language": "<primary language or \'unknown\'>",\n'
        '      "framework": "<primary framework or null>",\n'
        '      "purpose": "<one sentence on what the repo is for>",\n'
        '      "relevant_files": [\n'
        '        {"path": "<relative path>", "why": "<one sentence>"}\n'
        "      ],\n"
        '      "key_modules": ["<module or directory>"]\n'
        "    }\n"
        "  ],\n"
        '  "cross_repo_links": ["<one relationship per entry>"],\n'
        '  "task_relevant_areas": ["<one area of code per entry>"],\n'
        '  "open_questions": ["<one unknown per entry>"]\n'
        "}\n\n"
        "Only report files you actually read. Do not invent files.\n"
        "Focus on files relevant to the task."
    )

    architect_content = (
        "Your role: Architect.\n"
        "You have read-only tools to verify your understanding of the codebase: "
        "read_file, grep, list_files.\n\n"
        "Before producing a plan, use tools to read the specific files mentioned in "
        "the Tech Lead's context. Verify that APIs, imports, and patterns match what "
        "you expect so your plan is grounded in reality.\n\n"
        "Workflow:\n"
        "1. Read the files most relevant to the task from the context.\n"
        "2. Grep for related patterns if you need to understand usage or dependencies.\n"
        "3. When confident, call done() with the plan as a JSON object.\n\n"
        "The done() summary must be a JSON object with exactly this schema:\n"
        "{\n"
        '  "rationale": "<why this plan is the right approach>",\n'
        '  "repos": [\n'
        "    {\n"
        '      "name": "<repo name>",\n'
        '      "changes": [\n'
        "        {\n"
        '          "file": "<relative file path>",\n'
        '          "action": "create|modify|delete",\n'
        '          "description": "<specific, small description of exactly what to change>"\n'
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ],\n"
        '  "execution_order": ["<repo-a>", "<repo-b>"],\n'
        '  "risks": ["<one risk per entry>"]\n'
        "}\n\n"
        "STRICT RULES:\n"
        "- ONLY plan changes that directly address the user's task.\n"
        "- NEVER plan changes to database configs, CI/CD, or security settings unless "
        "the task specifically asks for it.\n"
        "- Prefer MODIFY over DELETE+CREATE.\n"
        "- Base every decision on files you actually read. Do not invent APIs."
    )

    senior_developer_content = (
        "Your role: Senior Developer.\n"
        "You have tools to read files, search code, and make edits. "
        "Use them iteratively:\n\n"
        "1. First READ the files you need to change to understand the current code.\n"
        "2. Then EDIT specific strings \u2014 use edit_file with exact old_string matches.\n"
        "3. After editing, READ the file again to verify your change looks correct.\n"
        "4. When all changes are done, call the done() tool with a summary.\n\n"
        "RULES:\n"
        "- Use edit_file for SMALL, targeted changes. Do not rewrite entire files.\n"
        "- The old_string must be an EXACT substring of the file. Copy it precisely.\n"
        "- Do NOT change database configs, framework imports, or unrelated settings."
    )

    code_reviewer_content = (
        "Your role: Code Reviewer.\n"
        "You have read-only tools to inspect the actual files on disk: read_file, "
        "grep, list_files.\n\n"
        "Instead of reviewing from a diff dump, read the changed files directly and "
        "check the surrounding code for correctness.\n\n"
        "Workflow:\n"
        "1. Read each changed file to see the current state on disk.\n"
        "2. Grep for related usage if you need to verify imports or API contracts.\n"
        "3. Check for: logic errors, missing error handling, broken imports, type "
        "mismatches, off-by-one mistakes, hardcoded secrets, style issues.\n"
        "4. When done, call done() with a JSON verdict.\n\n"
        "The done() summary must be a JSON object with exactly this schema:\n"
        "{\n"
        '  "verdict": "approve" or "request_changes",\n'
        '  "feedback": "<empty string if approved, or detailed feedback listing each issue>"\n'
        "}\n\n"
        "RULES:\n"
        "- Compare the actual code against the plan. Every planned change should be "
        "present and correctly implemented.\n"
        "- If everything looks good, approve.\n"
        "- If there are issues, provide clear, actionable feedback."
    )

    qa_engineer_content = (
        "Your role: QA Engineer.\n"
        "Before running tests you have read-only tools to understand the test "
        "structure: read_file, grep, list_files.\n\n"
        "Use them to:\n"
        "1. Find test files (grep for \"test_\" or list tests/ directories).\n"
        "2. Read conftest.py and key test files to understand fixtures and setup.\n"
        "3. Understand what the tests cover relative to the changed files.\n"
        "4. When done, call done() with a JSON summary of your findings.\n\n"
        "The done() summary must be a JSON object with this schema:\n"
        "{\n"
        '  "test_files": ["<path to test file>", "..."],\n'
        '  "conftest_found": true/false,\n'
        '  "fixtures": ["<fixture name>", "..."],\n'
        '  "coverage_notes": "<brief notes on what the tests cover>"\n'
        "}\n\n"
        "Focus on understanding test structure, not running tests."
    )

    release_manager_content = (
        "Your role: Release Manager.\n"
        "Your job is to write clear, informative pull request titles and descriptions "
        "that help human reviewers understand what changed and why.\n\n"
        "Operating rules:\n"
        "- Keep the title under 72 characters.\n"
        "- The body should summarise the changes, not repeat the full diff.\n"
        "- Mention which files were added, modified, or deleted.\n"
        "- If the task spans multiple repos, note cross-repo dependencies.\n"
        "- Reply with a single JSON object matching the schema in the user message. "
        "No prose outside the JSON. No markdown fences."
    )

    devops_engineer_content = (
        "Your role: DevOps Engineer.\n"
        "Your job is to diagnose CI failures and produce minimal, targeted code fixes. "
        "You receive the full CI log output, the files that were recently changed, and "
        "the original task description. Your goal is to make CI pass on the next run.\n\n"
        "Operating rules:\n"
        "- Read the CI logs carefully. Identify the root cause: build error, test "
        "failure, linting issue, type error, missing dependency, etc.\n"
        "- Only touch files that are directly related to the failure. Do not refactor "
        "unrelated code or make cosmetic changes.\n"
        "- If the failure is a test assertion, fix the code (not the test) unless the "
        "test expectation is clearly wrong based on the task description.\n"
        "- If the failure is a missing import or dependency, add it.\n"
        "- If the failure is a type error, fix the types.\n"
        "- For each file change, wrap the full file content in a tagged block:\n\n"
        '  <file path="relative/path/to/file.py" action="modify">\n'
        "  file content here\n"
        "  </file>\n\n"
        "- Emit one <file> block per changed file. Include the complete file content "
        "(not just the diff).\n"
        "- Do not wrap output in markdown fences or add any prose outside the file tags."
    )

    task_decomposer_content = (
        "Your role: Task Decomposer.\n"
        "Your job is to break a user's engineering task into small, focused subtasks "
        "that can each be implemented independently. The Architect and Senior Developer "
        "will process each subtask separately, so clarity and isolation matter.\n\n"
        "Rules for decomposition:\n\n"
        "1. Each subtask should touch 1-3 files maximum. If a subtask would affect more "
        "files, split it further.\n"
        "2. Each subtask must be self-contained \u2014 it should make sense on its own without "
        "needing context from other subtasks.\n"
        "3. Subtasks should be ordered logically \u2014 foundational changes first, dependent "
        "changes after.\n"
        "4. Do NOT create subtasks for trivial changes (adding a comment to an empty "
        "__init__.py, fixing a trailing newline).\n"
        "5. Do NOT create subtasks that change database configurations, framework "
        "choices, or deployment files unless the original task explicitly asks for it.\n"
        "6. Each subtask description should be specific and actionable \u2014 not vague.\n"
        '   BAD:  "Improve the depth module"\n'
        '   GOOD: "Add a module-level docstring and function docstrings with Args/Returns '
        'sections to app/depth.py (load_pipeline and estimate_depth functions)"\n'
        "7. Keep the total number of subtasks reasonable (3-10 for most tasks).\n\n"
        "Reply with a single JSON object matching the schema in the user message. "
        "No prose outside the JSON. No markdown fences."
    )

    # ------------------------------------------------------------------
    # Bulk insert all seed rows
    # ------------------------------------------------------------------

    op.bulk_insert(
        prompt_blocks,
        [
            # Shared blocks
            {
                "id": str(uuid4()),
                "key": "identity",
                "content": identity_content,
                "category": "shared",
                "agent_role": None,
                "priority": 0,
                "is_active": True,
            },
            {
                "id": str(uuid4()),
                "key": "safety_rules",
                "content": safety_content,
                "category": "shared",
                "agent_role": None,
                "priority": 10,
                "is_active": True,
            },
            {
                "id": str(uuid4()),
                "key": "tool_rules",
                "content": tool_rules_content,
                "category": "tool_rules",
                "agent_role": None,
                "priority": 20,
                "is_active": True,
            },
            # Role-specific blocks
            {
                "id": str(uuid4()),
                "key": "tech_lead_role",
                "content": tech_lead_content,
                "category": "role",
                "agent_role": "tech_lead",
                "priority": 5,
                "is_active": True,
            },
            {
                "id": str(uuid4()),
                "key": "architect_role",
                "content": architect_content,
                "category": "role",
                "agent_role": "architect",
                "priority": 5,
                "is_active": True,
            },
            {
                "id": str(uuid4()),
                "key": "senior_developer_role",
                "content": senior_developer_content,
                "category": "role",
                "agent_role": "senior_developer",
                "priority": 5,
                "is_active": True,
            },
            {
                "id": str(uuid4()),
                "key": "code_reviewer_role",
                "content": code_reviewer_content,
                "category": "role",
                "agent_role": "code_reviewer",
                "priority": 5,
                "is_active": True,
            },
            {
                "id": str(uuid4()),
                "key": "qa_engineer_role",
                "content": qa_engineer_content,
                "category": "role",
                "agent_role": "qa_engineer",
                "priority": 5,
                "is_active": True,
            },
            {
                "id": str(uuid4()),
                "key": "release_manager_role",
                "content": release_manager_content,
                "category": "role",
                "agent_role": "release_manager",
                "priority": 5,
                "is_active": True,
            },
            {
                "id": str(uuid4()),
                "key": "devops_engineer_role",
                "content": devops_engineer_content,
                "category": "role",
                "agent_role": "devops_engineer",
                "priority": 5,
                "is_active": True,
            },
            {
                "id": str(uuid4()),
                "key": "task_decomposer_role",
                "content": task_decomposer_content,
                "category": "role",
                "agent_role": "task_decomposer",
                "priority": 5,
                "is_active": True,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_prompt_blocks_agent_role", table_name="prompt_blocks")
    op.drop_table("prompt_blocks")
