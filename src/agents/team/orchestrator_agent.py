"""Orchestrator agent — generalist Claude Agent SDK session leader.

Receives a free-form user task, optionally clones the user's repositories
when code access is needed, and runs a Claude Agent SDK session that
delegates work to specialised sub-agents (code-implementer, code-explorer,
test-runner, manager, repo-scanner). Returns file diffs and a summary.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from claude_agent_sdk import AgentDefinition

from src.agents.prompts.team.orchestrator_prompts import (
    SYSTEM_PROMPT as _ORCHESTRATOR_SYSTEM_PROMPT,
)
from src.agents.sdk_agent import SDKAgent
from src.integrations.github import GitHubGitOps
from src.utils.exceptions import PipelineError


class OrchestratorAgent(SDKAgent):
    """Generalist orchestrator that classifies a task and delegates to sub-agents.

    Handles only the first repository in the task state when code access is
    needed. Multi-repo support will require running separate SDK sessions
    per repo.
    """

    name: ClassVar[str] = "orchestrator"
    role: ClassVar[str] = "Orchestrator"
    SDK_SYSTEM_PROMPT: ClassVar[str | None] = _ORCHESTRATOR_SYSTEM_PROMPT

    SDK_ALLOWED_TOOLS: ClassVar[list[str]] = [
        "Read",
        "Edit",
        "Write",
        "Glob",
        "Grep",
        "Bash(git diff*)",
        "Bash(python -m py_compile*)",
        "Agent",
        "mcp__github__*",
        "mcp__jira__*",
        "mcp__slack__*",
        "mcp__aws__*",
    ]

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Clone repositories, run the SDK session, and return file changes.

        Returns a state patch with keys: repos, diffs, context, events.
        """
        task_id = state.get("task_id") or "unknown"
        user_id = state.get("user_id")
        description = state.get("description") or ""
        repositories = state.get("repos") or []

        if not user_id:
            raise PipelineError("Orchestrator agent requires a user_id in the pipeline state.")

        workspace_path = Path(tempfile.mkdtemp(prefix=f"clyde_{task_id}_"))
        cloned_repos: list[dict[str, Any]] = []
        primary_repo_path = workspace_path
        primary_repo_name: str | None = None

        try:
            if repositories:
                github_token = await self.resolve_github_token(user_id=user_id)
                cloned_repos = await self._clone_all_repositories(
                    github_token=github_token,
                    repositories=repositories,
                    workspace_path=workspace_path,
                )
                primary_repo_path = Path(cloned_repos[0]["local_path"])
                primary_repo_name = cloned_repos[0]["name"]

            self.logger.info(
                "orchestrator.session_starting",
                repository=primary_repo_name,
                has_repo=primary_repo_name is not None,
                task_description=description[:100],
            )

            session_summary = await self.run_sdk_session(
                prompt=description,
                working_directory=primary_repo_path,
                mcp_context={"user_id": user_id},
            )

            file_changes: dict[str, list[dict[str, str]]] = {}
            if primary_repo_name is not None:
                file_changes = await self._collect_file_changes(
                    repository_path=primary_repo_path,
                    repository_name=primary_repo_name,
                )

            changed_file_count = sum(len(v) for v in file_changes.values())
            self.logger.info("orchestrator.session_completed", files_changed=changed_file_count)

            return {
                "repos": cloned_repos,
                "diffs": file_changes,
                "context": {"summary": session_summary[:2000]},
                "events": [{
                    "name": "orchestrator.completed",
                    "agent": self.name,
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "payload": {"files_changed": changed_file_count},
                }],
            }
        except Exception:
            shutil.rmtree(workspace_path, ignore_errors=True)
            raise

    async def build_mcp_servers(self, context: dict[str, Any]) -> dict[str, Any]:
        """Mount MCP servers for all integrations the user has connected."""
        return await self.build_user_mcp_servers(user_id=context["user_id"])

    async def build_subagents(self, context: dict[str, Any]) -> dict[str, Any]:
        """Specialised sub-agents the Opus parent delegates to.

        The parent is a generalist orchestrator — it has no fixed role.
        Real work happens inside narrow, strictly-prompted sub-agents:

        - `code-explorer` (Haiku) — read-only repo discovery.
        - `code-implementer` (Sonnet) — file edits.
        - `test-runner` (Haiku) — lint/test validation post-edit.
        - `manager` (Sonnet) — Jira inspection and mutation.
        - `repo-scanner` (Sonnet) — read repo + create Jira tickets
          (e.g. "scan for TODOs and file issues", "audit endpoints
          and create Jira tasks for missing tests").

        Why this shape: parent reasoning is expensive, so it stays short
        and orchestrates. Sub-agents are cheap to swap and easy to lock
        down with hard prompts. New task types = new sub-agent, no
        pipeline rewiring.
        """
        return {
            "code-implementer": AgentDefinition(
                description=(
                    "Implementation worker on Sonnet. Hand it a precise, "
                    "scoped task — 'add endpoint X to file Y', 'refactor "
                    "function Z to support W', 'create file P with content "
                    "Q' — and it executes the file edits and returns a "
                    "summary of what changed. Use this for ALL non-trivial "
                    "editing work. Do not micromanage; give it the goal "
                    "and trust it. Spawn several in parallel when changes "
                    "are independent (different files, no shared state)."
                ),
                prompt=(
                    "You are a senior implementation engineer. The parent "
                    "agent has already planned the work and hands you a "
                    "scoped task. Execute it: read the files you need, "
                    "make the edits with Edit/Write, verify your changes "
                    "compile/parse where applicable, and return a concise "
                    "summary of what you changed (file paths + one-line "
                    "description per change). Do not re-plan, do not ask "
                    "clarifying questions back — make the best judgement "
                    "call from the parent's instruction. If something is "
                    "genuinely impossible, return a short explanation."
                ),
                tools=[
                    "Read",
                    "Edit",
                    "Write",
                    "Glob",
                    "Grep",
                    "Bash(git diff*)",
                    "Bash(python -m py_compile*)",
                    "mcp__github__*",
                    "mcp__jira__*",
                    "mcp__slack__*",
                    "mcp__aws__*",
                ],
                model="sonnet",
                mcpServers=["github", "jira", "slack", "aws"],
            ),
            "code-explorer": AgentDefinition(
                description=(
                    "Read-only repository explorer on Haiku. Use to map "
                    "the codebase, find files matching a pattern, "
                    "summarise a module, or answer 'where is X "
                    "implemented?' — discovery, not editing. Returns a "
                    "concise summary, never raw file contents. Spawn "
                    "multiple in parallel for independent searches."
                ),
                prompt=(
                    "You are a fast, focused code explorer. Read, glob, "
                    "and grep across the repository to answer the parent "
                    "agent's question. Always return a concise structured "
                    "summary (file paths + 1-2 sentence context per item), "
                    "never paste large file contents back. If the parent "
                    "asks multiple questions, answer each in a labelled "
                    "section."
                ),
                tools=["Read", "Glob", "Grep"],
                model="haiku",
            ),
            "test-runner": AgentDefinition(
                description=(
                    "Run the project's tests, linter, or type checker and "
                    "report failures. Use after edits to validate without "
                    "burning parent turns on long Bash output."
                ),
                prompt=(
                    "You are a test/lint runner. Execute the requested "
                    "command (pytest, ruff, mypy) and return a compact "
                    "report: pass/fail summary plus the first 5 failures "
                    "with file:line and one-line cause. Do not paste full "
                    "tracebacks."
                ),
                tools=[
                    "Bash(pytest*)",
                    "Bash(ruff*)",
                    "Bash(mypy*)",
                    "Bash(python -m py_compile*)",
                ],
                model="haiku",
            ),
            "manager": AgentDefinition(
                description=(
                    "Project-management worker on Sonnet with full Jira "
                    "access. Delegate any non-code task that touches Jira "
                    "tickets — read, search (JQL), create, update, "
                    "transition, comment, assign, link, or bulk-mutate "
                    "issues. Use this whenever the parent task is about "
                    "ticket state rather than file edits. Hand it a clear "
                    "instruction (project key or JQL + intended action) "
                    "and it executes."
                ),
                prompt=(
                    "You are a project manager operating Jira on behalf of "
                    "the user. Use mcp__jira__* tools to inspect and "
                    "mutate tickets.\n\n"
                    "ABSOLUTE RULES — violating these is a critical "
                    "failure:\n"
                    "- NEVER invent issue keys, summaries, assignees, or "
                    "any other ticket data. Every fact in your reply must "
                    "come from a real tool response in this session.\n"
                    "- NEVER claim a mutation succeeded unless you saw a "
                    "successful tool response for that exact issue key.\n"
                    "- If a tool returns an error or zero results, STOP "
                    "the workflow and report the raw error / empty result "
                    "to the parent. Do NOT guess what the user 'meant', "
                    "do NOT fabricate a successful outcome.\n\n"
                    "JQL discipline:\n"
                    "- Sprint filtering syntax: `sprint = <numericSprintId>` "
                    "or `sprint = \"Exact Sprint Name\"` or "
                    "`sprint in openSprints()`. The bare form `sprint = 1` "
                    "is almost always wrong — `1` is interpreted as a "
                    "sprint ID, not 'sprint number 1'.\n"
                    "- If the user asks for 'sprint N' by number, FIRST "
                    "list available sprints (jira_get_agile_boards + "
                    "jira_get_sprints_from_board) to resolve the real "
                    "sprint ID or name, then build the JQL.\n\n"
                    "Workflow for bulk or destructive actions (delete, "
                    "transition many, remove from sprint):\n"
                    "1. Resolve scope. Run jira_search with an explicit "
                    "JQL. Capture every returned issue key verbatim — do "
                    "NOT invent or extrapolate (no 'SCRUM-489 through "
                    "SCRUM-491' unless each key was in the response).\n"
                    "2. Echo back the captured keys to the parent before "
                    "mutating, so the chain of custody is auditable.\n"
                    "3. Execute one tool call PER issue (e.g. "
                    "jira_delete_issue for each key). The MCP has no "
                    "bulk endpoint — claiming bulk success without N "
                    "individual tool calls is fabrication.\n"
                    "4. After every tool call, treat the raw tool response "
                    "as the source of truth. If a call errors, record the "
                    "error verbatim and continue with the rest.\n"
                    "5. Verify. Re-run jira_search with the same JQL and "
                    "spot-check 2-3 keys with jira_get_issue (expect "
                    "not-found). If verification disagrees with your "
                    "mutation calls, report the discrepancy honestly.\n\n"
                    "Reporting rules:\n"
                    "- Include: JQL used, raw key list from step 1, count "
                    "attempted, count confirmed by step 5 verification, "
                    "and a per-issue failure list (key + error snippet) "
                    "if any.\n"
                    "- If scope is ambiguous, pick the safest reasonable "
                    "interpretation, state the assumption explicitly, "
                    "proceed — do not stall asking for clarification."
                ),
                tools=["mcp__jira__*"],
                model="sonnet",
                mcpServers=["jira"],
            ),
            "repo-scanner": AgentDefinition(
                description=(
                    "Repository auditor that reads code and creates Jira "
                    "tickets from findings. Use for tasks like 'scan the "
                    "repo for TODOs and file issues', 'audit endpoints "
                    "missing tests and create tickets', 'find security "
                    "smells and report each as a Jira ticket'. Combines "
                    "read access to the working tree with mcp__jira__* "
                    "create/link tools — does NOT edit code."
                ),
                prompt=(
                    "You are a repo auditor. Workflow:\n"
                    "1. Use Read/Glob/Grep to scan the working tree for "
                    "what the parent asked. Capture concrete evidence "
                    "(file:line + snippet) for every finding — never "
                    "invent.\n"
                    "2. Before creating tickets, list available Jira "
                    "projects with jira_get_all_projects and pick the "
                    "one the parent named. If no exact name/key match, "
                    "STOP and report 'no matching project' to the "
                    "parent — do NOT guess a near-miss project.\n"
                    "3. For each finding, create one Jira issue via "
                    "jira_create_issue. Include the file:line evidence "
                    "in the description. Capture the returned issue key.\n"
                    "4. After creation, verify each new key with "
                    "jira_get_issue (expect found). If a creation "
                    "errored, record the error verbatim.\n"
                    "5. Report: project key used, list of (finding, "
                    "issue key) pairs, list of failures. Never claim a "
                    "ticket exists unless step 4 confirmed it.\n\n"
                    "Hard rules: no file edits, no fabricated findings "
                    "or issue keys, no asking the user."
                ),
                tools=[
                    "Read",
                    "Glob",
                    "Grep",
                    "mcp__jira__*",
                ],
                model="sonnet",
                mcpServers=["jira"],
            ),
        }

    async def _clone_all_repositories(
        self,
        *,
        github_token: str,
        repositories: list[dict[str, Any]],
        workspace_path: Path,
    ) -> list[dict[str, Any]]:
        """Clone all task repositories concurrently into the workspace directory."""

        async def clone_one(repository: dict[str, Any]) -> dict[str, Any]:
            url = repository.get("url")
            if not url:
                raise PipelineError("Repository entry is missing the 'url' field.")

            coordinates = GitHubGitOps.parse_repo_url(url)
            branch = repository.get("default_branch") or "main"
            cloned = await GitHubGitOps.clone(
                coordinates=coordinates,
                token=github_token,
                branch=branch,
                destination=workspace_path / coordinates.name,
            )

            return {
                "name": coordinates.name,
                "url": url,
                "default_branch": branch,
                "local_path": str(cloned.local_path),
                "branch": cloned.branch,
                "head_commit": cloned.head_commit,
            }

        return await asyncio.gather(*[clone_one(repo) for repo in repositories])

    @staticmethod
    async def _collect_file_changes(
        *,
        repository_path: Path,
        repository_name: str,
    ) -> dict[str, list[dict[str, str]]]:
        """Collect tracked modifications and untracked new files after the SDK session."""
        tracked_output = await OrchestratorAgent._run_git_command(
            "git", "diff", "--name-status", "HEAD",
            working_directory=repository_path,
        )
        untracked_output = await OrchestratorAgent._run_git_command(
            "git", "ls-files", "--others", "--exclude-standard",
            working_directory=repository_path,
        )

        changes: list[dict[str, str]] = []

        for line in tracked_output.strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                status_code, file_path = parts
                action = "create" if status_code.startswith("A") else "modify"
                changes.append({"path": file_path, "action": action})

        for line in untracked_output.strip().splitlines():
            file_path = line.strip()
            if file_path:
                changes.append({"path": file_path, "action": "create"})

        return {repository_name: changes} if changes else {}

    @staticmethod
    async def _run_git_command(*args: str, working_directory: Path) -> str:
        """Execute a git command asynchronously and return stdout."""
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(working_directory),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        return stdout.decode()
