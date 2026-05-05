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
from src.db.queries.agent_config_query import AgentConfigRepository
from src.db.session import db
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

    SDK_ALLOWED_TOOLS: ClassVar[list[str]] = []
    SYSTEM_TOOLS: ClassVar[list[str]] = [
        "Read", "Edit", "Write", "Glob", "Grep",
        "Bash(git diff*)", "Bash(python -m py_compile*)", "Agent",
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
        """Specialised sub-agents the orchestrator delegates to.

        Subagent config (description, system_prompt, model, allowed MCP tools)
        is loaded from the database. System tools (Read, Edit, Bash, etc.) are
        hardcoded here per subagent name and merged with the DB-driven MCP tools.
        """
        _system_tools: dict[str, list[str]] = {
            "code-implementer": ["Read", "Edit", "Write", "Glob", "Grep", "Bash(git diff*)", "Bash(python -m py_compile*)"],
            "code-explorer":    ["Read", "Glob", "Grep"],
            "test-runner":      ["Bash(pytest*)", "Bash(ruff*)", "Bash(mypy*)", "Bash(python -m py_compile*)"],
            "manager":          [],
            "repo-scanner":     ["Read", "Glob", "Grep"],
        }

        user_id: int | None = context.get("user_id")
        async with db.session_scope() as session:
            repo = AgentConfigRepository(session)
            subagents = await repo.list_subagents()
            connected = await repo._get_connected_providers(user_id) if user_id else set()

        result: dict[str, Any] = {}
        for subagent in subagents:
            system_tools = _system_tools.get(subagent.name, [])
            active_mcp_providers = [
                t.mcp_server.provider_name
                for t in subagent.tools
                if t.is_active and t.mcp_server.provider_name in connected
            ]
            mcp_patterns = [f"mcp__{p}__*" for p in active_mcp_providers]
            result[subagent.name] = AgentDefinition(
                description=subagent.description,
                prompt=subagent.system_prompt,
                tools=system_tools + mcp_patterns,
                model=subagent.model,
                mcpServers=active_mcp_providers or None,
            )
        return result

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
