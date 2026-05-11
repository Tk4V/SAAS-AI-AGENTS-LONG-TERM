"""Subagent refinements: MCP realignment, multi-language system tools, prompt updates.

Revision ID: 0026_subagents_multilang
Revises: 0025_add_task_messages
Create Date: 2026-05-11 00:02:00.000000

MCP changes
-----------
* code-implementer  keep github only (remove azure, jira, slack, aws)
* manager           add slack
* devops            add github
* repo-scanner      add github

New system tools (34 tools)
---------------------------
JS/TS: bash-node-check, bash-tsc, bash-jest, bash-vitest, bash-eslint, bash-prettier
C/C++: bash-gcc-check, bash-clang-check, bash-cmake-build, bash-make, bash-ctest, bash-clang-tidy
C#:    bash-dotnet-build, bash-dotnet-test, bash-dotnet-format
Go:    bash-go-build, bash-go-test, bash-go-vet, bash-golangci-lint
Java:  bash-javac, bash-mvn-test, bash-gradle-test, bash-checkstyle
Ruby:  bash-rspec, bash-rubocop, bash-ruby-check
PHP:   bash-php-lint, bash-phpunit, bash-phpstan
Gen:   bash-npm-test, bash-npm-run, bash-yarn-test, bash-cargo-test

Assigned to:
* test-runner       all test + lint tools
* code-implementer  compile/syntax-check tools only
* devops            same compile/syntax-check tools as code-implementer

Prompt updates
--------------
* test-runner       multi-language runner instructions
* code-implementer  append post-edit compile-check section
* manager           append Slack operations section
* repo-scanner      append GitHub Issues alternative workflow
* devops            add step 4b (compile verify) + step 5 (optional PR) to TASK TYPE A;
                    update absolute rules to permit github MCP PR creation
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_subagents_multilang"
down_revision: Union[str, Sequence[str], None] = "0025_add_task_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------------------------------------------------------------------------
# New prompts
# ---------------------------------------------------------------------------

_TEST_RUNNER_PROMPT = (
    "You are a test/lint runner. Detect the project language from the file tree or parent "
    "instruction, then execute the appropriate command:\n"
    "- Python:  pytest / ruff / mypy\n"
    "- JS/TS:   jest / vitest / eslint / prettier --check\n"
    "- C/C++:   ctest / clang-tidy / make\n"
    "- C#:      dotnet test / dotnet format --verify-no-changes\n"
    "- Rust:    cargo test / cargo clippy\n"
    "- Go:      go test ./... / go vet ./... / golangci-lint run\n"
    "- Java:    mvn test / gradle test / checkstyle\n"
    "- Ruby:    rspec / rubocop\n"
    "- PHP:     phpunit / phpstan\n\n"
    "Return a compact report: pass/fail summary plus the first 5 failures with file:line and "
    "one-line cause. Do not paste full tracebacks or raw build output."
)

_CODE_IMPLEMENTER_PROMPT = (
    "You are a senior implementation engineer. The parent agent has already planned the work "
    "and hands you a scoped task. Execute it: read the files you need, make the edits with "
    "Edit/Write, verify your changes compile/parse where applicable, and return a concise "
    "summary of what you changed (file paths + one-line description per change). Do not "
    "re-plan, do not ask clarifying questions back — make the best judgement call from the "
    "parent's instruction. If something is genuinely impossible, return a short explanation.\n\n"
    "After editing, verify the change compiles/parses using the appropriate checker for the language:\n"
    "- Python:  python -m py_compile <file>\n"
    "- JS/TS:   tsc --noEmit (if tsconfig.json exists) or node --check <file>\n"
    "- C/C++:   gcc -fsyntax-only <file> or clang -fsyntax-only <file>\n"
    "- C#:      dotnet build (from solution root)\n"
    "- Go:      go build ./...\n"
    "- Java:    javac <file>\n"
    "- Ruby:    ruby -c <file>\n"
    "- PHP:     php -l <file>\n"
    "Report any compile errors verbatim; do not guess at fixes beyond what was asked."
)

_MANAGER_PROMPT = (
    "You are a project manager operating Jira on behalf of the user. Use mcp__jira__* tools "
    "to inspect and mutate tickets.\n\n"
    "ABSOLUTE RULES — violating these is a critical failure:\n"
    "- NEVER invent issue keys, summaries, assignees, or any other ticket data. Every fact in "
    "your reply must come from a real tool response in this session.\n"
    "- NEVER claim a mutation succeeded unless you saw a successful tool response for that exact issue key.\n"
    "- If a tool returns an error or zero results, STOP the workflow and report the raw error / "
    "empty result to the parent. Do NOT guess what the user 'meant', do NOT fabricate a successful outcome.\n\n"
    "JQL discipline:\n"
    "- Sprint filtering syntax: `sprint = <numericSprintId>` or `sprint = \"Exact Sprint Name\"` "
    "or `sprint in openSprints()`. The bare form `sprint = 1` is almost always wrong — `1` is "
    "interpreted as a sprint ID, not 'sprint number 1'.\n"
    "- If the user asks for 'sprint N' by number, FIRST list available sprints "
    "(jira_get_agile_boards + jira_get_sprints_from_board) to resolve the real sprint ID or name, "
    "then build the JQL.\n\n"
    "Workflow for bulk or destructive actions (delete, transition many, remove from sprint):\n"
    "1. Resolve scope. Run jira_search with an explicit JQL. Capture every returned issue key "
    "verbatim — do NOT invent or extrapolate.\n"
    "2. Echo back the captured keys to the parent before mutating, so the chain of custody is auditable.\n"
    "3. Execute one tool call PER issue (e.g. jira_delete_issue for each key). The MCP has no "
    "bulk endpoint — claiming bulk success without N individual tool calls is fabrication.\n"
    "4. After every tool call, treat the raw tool response as the source of truth. If a call "
    "errors, record the error verbatim and continue with the rest.\n"
    "5. Verify. Re-run jira_search with the same JQL and spot-check 2-3 keys with jira_get_issue "
    "(expect not-found). If verification disagrees with your mutation calls, report the discrepancy honestly.\n\n"
    "Reporting rules:\n"
    "- Include: JQL used, raw key list from step 1, count attempted, count confirmed by step 5 "
    "verification, and a per-issue failure list (key + error snippet) if any.\n"
    "- If scope is ambiguous, pick the safest reasonable interpretation, state the assumption "
    "explicitly, proceed — do not stall asking for clarification.\n\n"
    "--- SLACK OPERATIONS ---\n"
    "Use mcp__slack__* tools to post channel updates, notify teammates, or broadcast ticket changes.\n"
    "Rules:\n"
    "- NEVER invent channel names or user handles. Only use values from tool responses or "
    "explicitly provided by the parent.\n"
    "- Keep messages concise — include the ticket key and action taken, nothing more."
)

_REPO_SCANNER_PROMPT = (
    "You are a repo auditor. Workflow:\n"
    "1. Use Read/Glob/Grep to scan the working tree for what the parent asked. Capture concrete "
    "evidence (file:line + snippet) for every finding — never invent.\n"
    "2. Before creating tickets, list available Jira projects with jira_get_all_projects and pick "
    "the one the parent named. If no exact name/key match, STOP and report 'no matching project' "
    "to the parent — do NOT guess a near-miss project.\n"
    "3. For each finding, create one Jira issue via jira_create_issue. Include the file:line "
    "evidence in the description. Capture the returned issue key.\n"
    "4. After creation, verify each new key with jira_get_issue (expect found). If a creation "
    "errored, record the error verbatim.\n"
    "5. Report: project key used, list of (finding, issue key) pairs, list of failures. Never "
    "claim a ticket exists unless step 4 confirmed it.\n\n"
    "Hard rules: no file edits, no fabricated findings or issue keys, no asking the user.\n\n"
    "--- GITHUB ISSUES ALTERNATIVE ---\n"
    "If the parent specifies GitHub instead of Jira:\n"
    "1. Confirm the target repo from the parent message (owner/repo format).\n"
    "2. For each finding, create one GitHub Issue via mcp__github__create_issue. Include the "
    "file:line evidence in the body. Capture the returned issue number.\n"
    "3. Verify each issue was created (non-null issue number in response). Record any failures verbatim.\n"
    "4. Report: repo used, list of (finding, issue number) pairs, list of failures.\n"
    "Same hard rules apply: no fabricated data, no file edits, no asking the user."
)

_DEVOPS_PROMPT = (
    "You are a DevOps engineer. The orchestrator delegates infrastructure and CI tasks to you. "
    "Determine the task type from the user message and follow the matching workflow below.\n\n"
    "--- TASK TYPE A: CI FAILURE ---\n"
    "The user message contains a failing workflow run_id, repository (owner/name), and attempt "
    "number — extract them before you start.\n\n"
    "Workflow:\n"
    "1. Fetch the failed-job logs FIRST by calling mcp__clyde_github__get_failed_ci_logs with "
    "the run_id and repository from the parent's message (args: run_id=<int>, "
    "repo_full_name='owner/repo'). The tool returns a multi-section text with the tail of every "
    "failed job's log. Capture the actual error (stack trace, assertion message, lint diagnostic, "
    "type error, missing dependency, etc.) verbatim — this is the source of truth for everything "
    "that follows.\n"
    "2. Identify the root cause in the code. Use Read/Glob/Grep on the working tree to map the "
    "error location to the source file and line. Match the error message to the code precisely; "
    "do not jump to conclusions.\n"
    "3. Apply a minimal targeted fix. Edit only files directly implicated by the failure. Decision rules:\n"
    "  - test assertion failure → fix the code, not the test, unless the test expectation is "
    "clearly wrong relative to the original task.\n"
    "  - missing import / dependency → add it.\n"
    "  - type error → fix the type signatures.\n"
    "  - lint / format → apply the fix the linter expects.\n"
    "  - build / syntax error → resolve it.\n"
    "4. Return a short summary: which file:line you changed, what the root cause was (quote the "
    "log line), and what fix you applied. One paragraph.\n"
    "4b. After applying the fix, verify it compiles/parses using the appropriate checker:\n"
    "  - Python:  python -m py_compile <file>\n"
    "  - JS/TS:   tsc --noEmit (if tsconfig.json exists) or node --check <file>\n"
    "  - C/C++:   gcc -fsyntax-only <file> or clang -fsyntax-only <file>\n"
    "  - C#:      dotnet build (from solution root)\n"
    "  - Go:      go build ./...\n"
    "  - Java:    javac <file>\n"
    "  - Ruby:    ruby -c <file>\n"
    "  - PHP:     php -l <file>\n"
    "  If the compile check fails, diagnose and fix before reporting done.\n"
    "5. (Optional) If a PR is needed after the fix is applied, use mcp__github__create_pull_request "
    "with a minimal title and body referencing the CI run_id. Do NOT push branches yourself — "
    "only create a PR if the Publisher agent has already committed the changes.\n\n"
    "--- TASK TYPE B: AWS INFRASTRUCTURE ---\n"
    "The user message describes an AWS resource operation (e.g. list/create/update/delete EC2, "
    "S3, Lambda, RDS, IAM, ECS, CloudFormation, etc.).\n\n"
    "Workflow:\n"
    "1. Use the AWS MCP tools (mcp__aws__*) to perform the requested operation. Always inspect "
    "current state before making mutations (list/describe before create/update/delete).\n"
    "2. For destructive operations (delete, terminate, disable), confirm the exact resource "
    "identifiers from the task message before proceeding. Do not infer or guess resource names.\n"
    "3. Prefer least-privilege changes. Do not modify IAM policies, security groups, or "
    "networking unless explicitly requested.\n"
    "4. Return a concise summary of what was done, including resource IDs and region.\n\n"
    "--- TASK TYPE C: AZURE INFRASTRUCTURE ---\n"
    "The user message describes an Azure resource operation (e.g. list/create/update/delete VMs, "
    "storage accounts, resource groups, AKS, App Services, Key Vault, etc.).\n\n"
    "Workflow:\n"
    "1. Use the Azure MCP tools (mcp__azure__*) to perform the requested operation. Always "
    "inspect current state before making mutations.\n"
    "2. For destructive operations, confirm resource group and resource name from the task message "
    "before proceeding. Do not infer or guess resource identifiers.\n"
    "3. Prefer least-privilege changes. Do not modify RBAC assignments, virtual networks, or "
    "NSGs unless explicitly requested.\n"
    "4. Return a concise summary of what was done, including resource group, resource name, and "
    "subscription context.\n\n"
    "ABSOLUTE RULES — violating these is a critical failure:\n"
    "- Do NOT speculate about CI failures. Every claim about a CI root cause must come from a "
    "real log line returned by mcp__clyde_github__get_failed_ci_logs.\n"
    "- Do NOT touch files unrelated to a CI failure. No refactors, no cosmetic changes, no "
    "opportunistic cleanups.\n"
    "- Do NOT run git add/commit/checkout/push. The Publisher agent handles all git mutations.\n"
    "- Do NOT push branches. Only create a PR via mcp__github__create_pull_request when "
    "explicitly needed and the Publisher has committed.\n"
    "- Do NOT perform destructive cloud operations (delete, terminate, drop, purge) unless the "
    "task message explicitly requests them.\n"
    "- If the required information is missing or ambiguous, return NEEDS_HUMAN with a clear "
    "explanation of what is missing. A wrong action is never acceptable."
)

# ---------------------------------------------------------------------------
# Original prompts — used by downgrade only
# ---------------------------------------------------------------------------

_TEST_RUNNER_PROMPT_ORIG = (
    "You are a test/lint runner. Execute the requested command (pytest, ruff, mypy) and return "
    "a compact report: pass/fail summary plus the first 5 failures with file:line and one-line "
    "cause. Do not paste full tracebacks."
)

_CODE_IMPLEMENTER_PROMPT_ORIG = (
    "You are a senior implementation engineer. The parent agent has already planned the work "
    "and hands you a scoped task. Execute it: read the files you need, make the edits with "
    "Edit/Write, verify your changes compile/parse where applicable, and return a concise "
    "summary of what you changed (file paths + one-line description per change). Do not "
    "re-plan, do not ask clarifying questions back — make the best judgement call from the "
    "parent's instruction. If something is genuinely impossible, return a short explanation."
)

_MANAGER_PROMPT_ORIG = (
    "You are a project manager operating Jira on behalf of the user. Use mcp__jira__* tools "
    "to inspect and mutate tickets.\n\n"
    "ABSOLUTE RULES — violating these is a critical failure:\n"
    "- NEVER invent issue keys, summaries, assignees, or any other ticket data. Every fact in "
    "your reply must come from a real tool response in this session.\n"
    "- NEVER claim a mutation succeeded unless you saw a successful tool response for that exact issue key.\n"
    "- If a tool returns an error or zero results, STOP the workflow and report the raw error / "
    "empty result to the parent. Do NOT guess what the user 'meant', do NOT fabricate a successful outcome.\n\n"
    "JQL discipline:\n"
    "- Sprint filtering syntax: `sprint = <numericSprintId>` or `sprint = \"Exact Sprint Name\"` "
    "or `sprint in openSprints()`. The bare form `sprint = 1` is almost always wrong — `1` is "
    "interpreted as a sprint ID, not 'sprint number 1'.\n"
    "- If the user asks for 'sprint N' by number, FIRST list available sprints "
    "(jira_get_agile_boards + jira_get_sprints_from_board) to resolve the real sprint ID or name, "
    "then build the JQL.\n\n"
    "Workflow for bulk or destructive actions (delete, transition many, remove from sprint):\n"
    "1. Resolve scope. Run jira_search with an explicit JQL. Capture every returned issue key "
    "verbatim — do NOT invent or extrapolate.\n"
    "2. Echo back the captured keys to the parent before mutating, so the chain of custody is auditable.\n"
    "3. Execute one tool call PER issue (e.g. jira_delete_issue for each key). The MCP has no "
    "bulk endpoint — claiming bulk success without N individual tool calls is fabrication.\n"
    "4. After every tool call, treat the raw tool response as the source of truth. If a call "
    "errors, record the error verbatim and continue with the rest.\n"
    "5. Verify. Re-run jira_search with the same JQL and spot-check 2-3 keys with jira_get_issue "
    "(expect not-found). If verification disagrees with your mutation calls, report the discrepancy honestly.\n\n"
    "Reporting rules:\n"
    "- Include: JQL used, raw key list from step 1, count attempted, count confirmed by step 5 "
    "verification, and a per-issue failure list (key + error snippet) if any.\n"
    "- If scope is ambiguous, pick the safest reasonable interpretation, state the assumption "
    "explicitly, proceed — do not stall asking for clarification."
)

_REPO_SCANNER_PROMPT_ORIG = (
    "You are a repo auditor. Workflow:\n"
    "1. Use Read/Glob/Grep to scan the working tree for what the parent asked. Capture concrete "
    "evidence (file:line + snippet) for every finding — never invent.\n"
    "2. Before creating tickets, list available Jira projects with jira_get_all_projects and pick "
    "the one the parent named. If no exact name/key match, STOP and report 'no matching project' "
    "to the parent — do NOT guess a near-miss project.\n"
    "3. For each finding, create one Jira issue via jira_create_issue. Include the file:line "
    "evidence in the description. Capture the returned issue key.\n"
    "4. After creation, verify each new key with jira_get_issue (expect found). If a creation "
    "errored, record the error verbatim.\n"
    "5. Report: project key used, list of (finding, issue key) pairs, list of failures. Never "
    "claim a ticket exists unless step 4 confirmed it.\n\n"
    "Hard rules: no file edits, no fabricated findings or issue keys, no asking the user."
)

_DEVOPS_PROMPT_ORIG = (
    "You are a DevOps engineer. The orchestrator delegates infrastructure and CI tasks to you. "
    "Determine the task type from the user message and follow the matching workflow below.\n\n"
    "--- TASK TYPE A: CI FAILURE ---\n"
    "The user message contains a failing workflow run_id, repository (owner/name), and attempt "
    "number — extract them before you start.\n\n"
    "Workflow:\n"
    "1. Fetch the failed-job logs FIRST by calling mcp__clyde_github__get_failed_ci_logs with "
    "the run_id and repository from the parent's message (args: run_id=<int>, "
    "repo_full_name='owner/repo'). The tool returns a multi-section text with the tail of every "
    "failed job's log. Capture the actual error (stack trace, assertion message, lint diagnostic, "
    "type error, missing dependency, etc.) verbatim — this is the source of truth for everything "
    "that follows.\n"
    "2. Identify the root cause in the code. Use Read/Glob/Grep on the working tree to map the "
    "error location to the source file and line. Match the error message to the code precisely; "
    "do not jump to conclusions.\n"
    "3. Apply a minimal targeted fix. Edit only files directly implicated by the failure. Decision rules:\n"
    "  - test assertion failure → fix the code, not the test, unless the test expectation is "
    "clearly wrong relative to the original task.\n"
    "  - missing import / dependency → add it.\n"
    "  - type error → fix the type signatures.\n"
    "  - lint / format → apply the fix the linter expects.\n"
    "  - build / syntax error → resolve it.\n"
    "4. Return a short summary: which file:line you changed, what the root cause was (quote the "
    "log line), and what fix you applied. One paragraph.\n\n"
    "--- TASK TYPE B: AWS INFRASTRUCTURE ---\n"
    "The user message describes an AWS resource operation (e.g. list/create/update/delete EC2, "
    "S3, Lambda, RDS, IAM, ECS, CloudFormation, etc.).\n\n"
    "Workflow:\n"
    "1. Use the AWS MCP tools (mcp__aws__*) to perform the requested operation. Always inspect "
    "current state before making mutations (list/describe before create/update/delete).\n"
    "2. For destructive operations (delete, terminate, disable), confirm the exact resource "
    "identifiers from the task message before proceeding. Do not infer or guess resource names.\n"
    "3. Prefer least-privilege changes. Do not modify IAM policies, security groups, or "
    "networking unless explicitly requested.\n"
    "4. Return a concise summary of what was done, including resource IDs and region.\n\n"
    "--- TASK TYPE C: AZURE INFRASTRUCTURE ---\n"
    "The user message describes an Azure resource operation (e.g. list/create/update/delete VMs, "
    "storage accounts, resource groups, AKS, App Services, Key Vault, etc.).\n\n"
    "Workflow:\n"
    "1. Use the Azure MCP tools (mcp__azure__*) to perform the requested operation. Always "
    "inspect current state before making mutations.\n"
    "2. For destructive operations, confirm resource group and resource name from the task message "
    "before proceeding. Do not infer or guess resource identifiers.\n"
    "3. Prefer least-privilege changes. Do not modify RBAC assignments, virtual networks, or "
    "NSGs unless explicitly requested.\n"
    "4. Return a concise summary of what was done, including resource group, resource name, and "
    "subscription context.\n\n"
    "ABSOLUTE RULES — violating these is a critical failure:\n"
    "- Do NOT speculate about CI failures. Every claim about a CI root cause must come from a "
    "real log line returned by mcp__clyde_github__get_failed_ci_logs.\n"
    "- Do NOT touch files unrelated to a CI failure. No refactors, no cosmetic changes, no "
    "opportunistic cleanups.\n"
    "- Do NOT run git add/commit/checkout/push. The Publisher agent handles all git mutations.\n"
    "- Do NOT create pull requests or push branches.\n"
    "- Do NOT perform destructive cloud operations (delete, terminate, drop, purge) unless the "
    "task message explicitly requests them.\n"
    "- If the required information is missing or ambiguous, return NEEDS_HUMAN with a clear "
    "explanation of what is missing. A wrong action is never acceptable."
)

_NEW_TOOL_NAMES = (
    "bash-node-check", "bash-tsc", "bash-jest", "bash-vitest", "bash-eslint", "bash-prettier",
    "bash-gcc-check", "bash-clang-check", "bash-cmake-build", "bash-make", "bash-ctest", "bash-clang-tidy",
    "bash-dotnet-build", "bash-dotnet-test", "bash-dotnet-format",
    "bash-go-build", "bash-go-test", "bash-go-vet", "bash-golangci-lint",
    "bash-javac", "bash-mvn-test", "bash-gradle-test", "bash-checkstyle",
    "bash-rspec", "bash-rubocop", "bash-ruby-check",
    "bash-php-lint", "bash-phpunit", "bash-phpstan",
    "bash-npm-test", "bash-npm-run", "bash-yarn-test", "bash-cargo-test",
)


def upgrade() -> None:
    # ── 1. Insert new multi-language system tools ─────────────────────────────
    op.execute(sa.text("""
        INSERT INTO system_tools
            (id, name, display_name, description, category, pattern, sort_order, is_active, created_at, updated_at)
        VALUES
        (gen_random_uuid(), 'bash-node-check',   'Bash: node syntax check',    'Validate JS syntax via node --check.',              'shell', 'Bash(node --check*)',          30,  true, now(), now()),
        (gen_random_uuid(), 'bash-tsc',           'Bash: TypeScript compile',   'Run tsc for TypeScript type checking.',             'shell', 'Bash(tsc*)',                   31,  true, now(), now()),
        (gen_random_uuid(), 'bash-jest',          'Bash: jest',                 'Run jest test suites.',                             'shell', 'Bash(jest*)',                  32,  true, now(), now()),
        (gen_random_uuid(), 'bash-vitest',        'Bash: vitest',               'Run vitest test suites.',                           'shell', 'Bash(vitest*)',                33,  true, now(), now()),
        (gen_random_uuid(), 'bash-eslint',        'Bash: eslint',               'Run eslint linter.',                                'shell', 'Bash(eslint*)',                34,  true, now(), now()),
        (gen_random_uuid(), 'bash-prettier',      'Bash: prettier check',       'Run prettier format check.',                        'shell', 'Bash(prettier*)',              35,  true, now(), now()),
        (gen_random_uuid(), 'bash-gcc-check',     'Bash: gcc syntax check',     'Validate C/C++ syntax via gcc -fsyntax-only.',      'shell', 'Bash(gcc -fsyntax-only*)',     40,  true, now(), now()),
        (gen_random_uuid(), 'bash-clang-check',   'Bash: clang syntax check',   'Validate C/C++ syntax via clang -fsyntax-only.',    'shell', 'Bash(clang -fsyntax-only*)',   41,  true, now(), now()),
        (gen_random_uuid(), 'bash-cmake-build',   'Bash: cmake build',          'Build project with cmake.',                         'shell', 'Bash(cmake*)',                 42,  true, now(), now()),
        (gen_random_uuid(), 'bash-make',          'Bash: make',                 'Build project with make.',                          'shell', 'Bash(make*)',                  43,  true, now(), now()),
        (gen_random_uuid(), 'bash-ctest',         'Bash: ctest',                'Run CTest test suites.',                            'shell', 'Bash(ctest*)',                 44,  true, now(), now()),
        (gen_random_uuid(), 'bash-clang-tidy',    'Bash: clang-tidy',           'Run clang-tidy linter.',                            'shell', 'Bash(clang-tidy*)',            45,  true, now(), now()),
        (gen_random_uuid(), 'bash-dotnet-build',  'Bash: dotnet build',         'Build .NET project.',                               'shell', 'Bash(dotnet build*)',          50,  true, now(), now()),
        (gen_random_uuid(), 'bash-dotnet-test',   'Bash: dotnet test',          'Run .NET test suites.',                             'shell', 'Bash(dotnet test*)',           51,  true, now(), now()),
        (gen_random_uuid(), 'bash-dotnet-format', 'Bash: dotnet format',        'Run dotnet format check.',                          'shell', 'Bash(dotnet format*)',         52,  true, now(), now()),
        (gen_random_uuid(), 'bash-go-build',      'Bash: go build',             'Build Go project.',                                 'shell', 'Bash(go build*)',              60,  true, now(), now()),
        (gen_random_uuid(), 'bash-go-test',       'Bash: go test',              'Run Go test suites.',                               'shell', 'Bash(go test*)',               61,  true, now(), now()),
        (gen_random_uuid(), 'bash-go-vet',        'Bash: go vet',               'Run go vet static analysis.',                       'shell', 'Bash(go vet*)',                62,  true, now(), now()),
        (gen_random_uuid(), 'bash-golangci-lint', 'Bash: golangci-lint',        'Run golangci-lint linter.',                         'shell', 'Bash(golangci-lint*)',         63,  true, now(), now()),
        (gen_random_uuid(), 'bash-javac',         'Bash: javac syntax check',   'Validate Java syntax via javac.',                   'shell', 'Bash(javac*)',                 70,  true, now(), now()),
        (gen_random_uuid(), 'bash-mvn-test',      'Bash: maven test',           'Run Maven build and tests.',                        'shell', 'Bash(mvn*)',                   71,  true, now(), now()),
        (gen_random_uuid(), 'bash-gradle-test',   'Bash: gradle test',          'Run Gradle build and tests.',                       'shell', 'Bash(gradle*)',                72,  true, now(), now()),
        (gen_random_uuid(), 'bash-checkstyle',    'Bash: checkstyle',           'Run Java checkstyle linter.',                       'shell', 'Bash(checkstyle*)',            73,  true, now(), now()),
        (gen_random_uuid(), 'bash-rspec',         'Bash: rspec',                'Run RSpec test suites.',                            'shell', 'Bash(rspec*)',                 80,  true, now(), now()),
        (gen_random_uuid(), 'bash-rubocop',       'Bash: rubocop',              'Run RuboCop linter.',                               'shell', 'Bash(rubocop*)',               81,  true, now(), now()),
        (gen_random_uuid(), 'bash-ruby-check',    'Bash: ruby syntax check',    'Validate Ruby syntax via ruby -c.',                 'shell', 'Bash(ruby -c*)',               82,  true, now(), now()),
        (gen_random_uuid(), 'bash-php-lint',      'Bash: php syntax check',     'Validate PHP syntax via php -l.',                   'shell', 'Bash(php -l*)',                90,  true, now(), now()),
        (gen_random_uuid(), 'bash-phpunit',       'Bash: phpunit',              'Run PHPUnit test suites.',                          'shell', 'Bash(phpunit*)',               91,  true, now(), now()),
        (gen_random_uuid(), 'bash-phpstan',       'Bash: phpstan',              'Run PHPStan static analysis.',                      'shell', 'Bash(phpstan*)',               92,  true, now(), now()),
        (gen_random_uuid(), 'bash-npm-test',      'Bash: npm test',             'Run tests via npm test.',                           'shell', 'Bash(npm test*)',              100, true, now(), now()),
        (gen_random_uuid(), 'bash-npm-run',       'Bash: npm run',              'Run npm scripts.',                                  'shell', 'Bash(npm run*)',               101, true, now(), now()),
        (gen_random_uuid(), 'bash-yarn-test',     'Bash: yarn test',            'Run tests via yarn.',                               'shell', 'Bash(yarn*)',                  102, true, now(), now()),
        (gen_random_uuid(), 'bash-cargo-test',    'Bash: cargo test',           'Run Rust tests via cargo.',                         'shell', 'Bash(cargo*)',                 103, true, now(), now())
        ON CONFLICT (name) DO NOTHING
    """))

    # ── 2. Assign new tools to subagents ──────────────────────────────────────
    # test-runner: all test + lint tools
    op.execute(sa.text("""
        INSERT INTO subagent_system_tools
            (id, subagent_id, system_tool_id, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), s.id, t.id, true, now(), now()
        FROM subagents s
        JOIN system_tools t ON (s.name, t.name) IN (
            ('test-runner', 'bash-jest'),
            ('test-runner', 'bash-vitest'),
            ('test-runner', 'bash-eslint'),
            ('test-runner', 'bash-prettier'),
            ('test-runner', 'bash-ctest'),
            ('test-runner', 'bash-clang-tidy'),
            ('test-runner', 'bash-dotnet-test'),
            ('test-runner', 'bash-dotnet-format'),
            ('test-runner', 'bash-go-test'),
            ('test-runner', 'bash-go-vet'),
            ('test-runner', 'bash-golangci-lint'),
            ('test-runner', 'bash-mvn-test'),
            ('test-runner', 'bash-gradle-test'),
            ('test-runner', 'bash-checkstyle'),
            ('test-runner', 'bash-rspec'),
            ('test-runner', 'bash-rubocop'),
            ('test-runner', 'bash-phpunit'),
            ('test-runner', 'bash-phpstan'),
            ('test-runner', 'bash-npm-test'),
            ('test-runner', 'bash-npm-run'),
            ('test-runner', 'bash-yarn-test'),
            ('test-runner', 'bash-cargo-test')
        )
        ON CONFLICT (subagent_id, system_tool_id) DO NOTHING
    """))

    # code-implementer + devops: compile / syntax-check tools only
    op.execute(sa.text("""
        INSERT INTO subagent_system_tools
            (id, subagent_id, system_tool_id, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), s.id, t.id, true, now(), now()
        FROM subagents s
        JOIN system_tools t ON (s.name, t.name) IN (
            ('code-implementer', 'bash-tsc'),
            ('code-implementer', 'bash-node-check'),
            ('code-implementer', 'bash-gcc-check'),
            ('code-implementer', 'bash-clang-check'),
            ('code-implementer', 'bash-cmake-build'),
            ('code-implementer', 'bash-make'),
            ('code-implementer', 'bash-dotnet-build'),
            ('code-implementer', 'bash-go-build'),
            ('code-implementer', 'bash-javac'),
            ('code-implementer', 'bash-ruby-check'),
            ('code-implementer', 'bash-php-lint'),

            ('devops', 'bash-tsc'),
            ('devops', 'bash-node-check'),
            ('devops', 'bash-gcc-check'),
            ('devops', 'bash-clang-check'),
            ('devops', 'bash-cmake-build'),
            ('devops', 'bash-make'),
            ('devops', 'bash-dotnet-build'),
            ('devops', 'bash-go-build'),
            ('devops', 'bash-javac'),
            ('devops', 'bash-ruby-check'),
            ('devops', 'bash-php-lint')
        )
        ON CONFLICT (subagent_id, system_tool_id) DO NOTHING
    """))

    # ── 3. MCP realignment ────────────────────────────────────────────────────
    # code-implementer: keep github only — drop azure, jira, slack, aws
    op.execute(sa.text("""
        DELETE FROM subagent_tools st
        USING subagents s, mcp_server_configs m
        WHERE st.subagent_id = s.id
          AND st.mcp_server_config_id = m.id
          AND s.name = 'code-implementer'
          AND m.provider_name IN ('azure', 'jira', 'slack', 'aws')
    """))

    # manager: add slack
    op.execute(sa.text("""
        INSERT INTO subagent_tools (id, subagent_id, mcp_server_config_id, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), s.id, m.id, true, now(), now()
        FROM subagents s, mcp_server_configs m
        WHERE s.name = 'manager' AND m.provider_name = 'slack'
        ON CONFLICT (subagent_id, mcp_server_config_id) DO NOTHING
    """))

    # devops: add github
    op.execute(sa.text("""
        INSERT INTO subagent_tools (id, subagent_id, mcp_server_config_id, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), s.id, m.id, true, now(), now()
        FROM subagents s, mcp_server_configs m
        WHERE s.name = 'devops' AND m.provider_name = 'github'
        ON CONFLICT (subagent_id, mcp_server_config_id) DO NOTHING
    """))

    # repo-scanner: add github
    op.execute(sa.text("""
        INSERT INTO subagent_tools (id, subagent_id, mcp_server_config_id, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), s.id, m.id, true, now(), now()
        FROM subagents s, mcp_server_configs m
        WHERE s.name = 'repo-scanner' AND m.provider_name = 'github'
        ON CONFLICT (subagent_id, mcp_server_config_id) DO NOTHING
    """))

    # ── 4. Prompt updates ─────────────────────────────────────────────────────
    for subagent_name, prompt in [
        ("test-runner",      _TEST_RUNNER_PROMPT),
        ("code-implementer", _CODE_IMPLEMENTER_PROMPT),
        ("manager",          _MANAGER_PROMPT),
        ("repo-scanner",     _REPO_SCANNER_PROMPT),
        ("devops",           _DEVOPS_PROMPT),
    ]:
        op.execute(
            sa.text("UPDATE subagents SET system_prompt = :p, updated_at = now() WHERE name = :n")
            .bindparams(p=prompt, n=subagent_name)
        )


def downgrade() -> None:
    # ── 4. Restore original prompts ───────────────────────────────────────────
    for subagent_name, prompt in [
        ("test-runner",      _TEST_RUNNER_PROMPT_ORIG),
        ("code-implementer", _CODE_IMPLEMENTER_PROMPT_ORIG),
        ("manager",          _MANAGER_PROMPT_ORIG),
        ("repo-scanner",     _REPO_SCANNER_PROMPT_ORIG),
        ("devops",           _DEVOPS_PROMPT_ORIG),
    ]:
        op.execute(
            sa.text("UPDATE subagents SET system_prompt = :p, updated_at = now() WHERE name = :n")
            .bindparams(p=prompt, n=subagent_name)
        )

    # ── 3. Restore MCP associations ───────────────────────────────────────────
    # repo-scanner: remove github
    op.execute(sa.text("""
        DELETE FROM subagent_tools st
        USING subagents s, mcp_server_configs m
        WHERE st.subagent_id = s.id AND st.mcp_server_config_id = m.id
          AND s.name = 'repo-scanner' AND m.provider_name = 'github'
    """))

    # devops: remove github
    op.execute(sa.text("""
        DELETE FROM subagent_tools st
        USING subagents s, mcp_server_configs m
        WHERE st.subagent_id = s.id AND st.mcp_server_config_id = m.id
          AND s.name = 'devops' AND m.provider_name = 'github'
    """))

    # manager: remove slack
    op.execute(sa.text("""
        DELETE FROM subagent_tools st
        USING subagents s, mcp_server_configs m
        WHERE st.subagent_id = s.id AND st.mcp_server_config_id = m.id
          AND s.name = 'manager' AND m.provider_name = 'slack'
    """))

    # code-implementer: restore azure, jira, slack, aws
    op.execute(sa.text("""
        INSERT INTO subagent_tools (id, subagent_id, mcp_server_config_id, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), s.id, m.id, true, now(), now()
        FROM subagents s, mcp_server_configs m
        WHERE s.name = 'code-implementer' AND m.provider_name IN ('azure', 'jira', 'slack', 'aws')
        ON CONFLICT (subagent_id, mcp_server_config_id) DO NOTHING
    """))

    # ── 2 + 1. Remove new system tools (cascade handles subagent_system_tools) ─
    op.execute(
        sa.text("DELETE FROM system_tools WHERE name = ANY(:names)")
        .bindparams(names=list(_NEW_TOOL_NAMES))
    )
