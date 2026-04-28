"""Developer agent — autonomous code editor powered by Claude Agent SDK.

Clones the user's repositories, launches a Claude Agent SDK session scoped
to the cloned workspace, and collects the resulting file changes as git diffs.
The SDK session has access to file-system tools (Read, Edit, Write, Glob, Grep)
and limited Bash commands, allowing it to explore code, plan changes, and
implement them — exactly like a human developer using Claude Code.
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

from src.agents.sdk_agent import SDKAgent
from src.integrations.github import GitHubGitOps
from src.utils.exceptions import PipelineError


class DeveloperAgent(SDKAgent):
    """Autonomous developer that explores, plans, and edits code via Claude Agent SDK.

    The agent handles only the first repository in the task state. Multi-repo
    support will require running separate SDK sessions per repo.
    """

    name: ClassVar[str] = "developer"
    role: ClassVar[str] = "Developer"

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
            raise PipelineError("Developer agent requires a user_id in the pipeline state.")
        if not repositories:
            raise PipelineError("Developer agent requires at least one repository.")

        github_token = await self.resolve_github_token(user_id=user_id)
        workspace_path = Path(tempfile.mkdtemp(prefix=f"clyde_{task_id}_"))

        try:
            cloned_repos = await self._clone_all_repositories(
                github_token=github_token,
                repositories=repositories,
                workspace_path=workspace_path,
            )

            primary_repo_path = Path(cloned_repos[0]["local_path"])
            primary_repo_name = cloned_repos[0]["name"]

            self.logger.info(
                "developer.session_starting",
                repository=primary_repo_name,
                task_description=description[:100],
            )

            session_summary = await self.run_sdk_session(
                prompt=description,
                working_directory=primary_repo_path,
                mcp_context={"user_id": user_id},
            )

            file_changes = await self._collect_file_changes(
                repository_path=primary_repo_path,
                repository_name=primary_repo_name,
            )

            changed_file_count = len(file_changes.get(primary_repo_name, []))
            self.logger.info("developer.session_completed", files_changed=changed_file_count)

            return {
                "repos": cloned_repos,
                "diffs": file_changes,
                "context": {"summary": session_summary[:2000]},
                "events": [{
                    "name": "developer.completed",
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

        Hierarchy:
        - Parent (Opus 4.7): plans, decomposes, delegates. Few turns, all
          orchestration.
        - `code-implementer` (Sonnet 4.6): the workhorse — actually edits
          and writes files. Receives a precise instruction from the parent
          and produces the change. This is where most LLM tokens are spent.
        - `code-explorer` (Haiku 4.5): cheap, parallelisable exploration.
          Map the repo, find files matching X, summarise a module.
        - `test-runner` (Haiku 4.5): validate after edits.

        Why three tiers: Opus reasoning is expensive — let it think hard
        and short. Sonnet handles careful editing. Haiku does the wide
        cheap stuff. The SDK runs Agent calls in parallel when the parent
        spawns several at once, so this also wins wall-clock on
        independent tasks.
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
                ],
                model="sonnet",
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
        tracked_output = await DeveloperAgent._run_git_command(
            "git", "diff", "--name-status", "HEAD",
            working_directory=repository_path,
        )
        untracked_output = await DeveloperAgent._run_git_command(
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
