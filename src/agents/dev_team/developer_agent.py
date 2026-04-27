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

from src.agents.sdk_agent import SDKAgent
from src.utils.exceptions import PipelineError
from src.db.models.project import GitProviderKind
from src.agent_tools.mcp import github_mcp_server


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
                mcp_context={"github_token": github_token},
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
        """Inject the GitHub MCP server, authenticated with the per-task token."""
        github_token = context.get("github_token")
        if not github_token:
            raise PipelineError(
                "DeveloperAgent.build_mcp_servers requires a 'github_token' in mcp_context.",
            )
        return {"github": github_mcp_server(github_token)}

    async def _clone_all_repositories(
        self,
        *,
        github_token: str,
        repositories: list[dict[str, Any]],
        workspace_path: Path,
    ) -> list[dict[str, Any]]:
        """Clone all task repositories concurrently into the workspace directory."""
        git_provider = self.toolbox.git.for_kind(GitProviderKind.GITHUB)

        async def clone_one(repository: dict[str, Any]) -> dict[str, Any]:
            url = repository.get("url")
            if not url:
                raise PipelineError("Repository entry is missing the 'url' field.")

            coordinates = git_provider.parse_repo_url(url)
            branch = repository.get("default_branch") or "main"
            cloned = await git_provider.clone(
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
