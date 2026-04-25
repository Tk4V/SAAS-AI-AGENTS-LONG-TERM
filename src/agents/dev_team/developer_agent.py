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

from src.agents.base_agent import BaseAgent
from src.agents.prompts.dev_team.developer_prompts import (
    SDK_ALLOWED_TOOLS,
    SDK_MAX_TURNS,
    SDK_MODEL,
    SDK_PERMISSION_MODE,
)
from src.utils.exceptions import PipelineError
from src.db.models.project import GitProviderKind
from src.tools import toolbox
from src.tools.custom_tools.git.git_factory import GitProviderFactory


class DeveloperAgent(BaseAgent):
    """Autonomous developer that explores, plans, and edits code via Claude Agent SDK.

    The agent handles only the first repository in the task state. Multi-repo
    support will require running separate SDK sessions per repo.
    """

    name: ClassVar[str] = "developer"
    role: ClassVar[str] = "Developer"

    def __init__(
        self,
        *,
        git_factory: GitProviderFactory | None = None,
    ) -> None:
        super().__init__()
        self._git_factory = git_factory or toolbox.git

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

            session_summary = await self._run_sdk_session(
                task_description=description,
                working_directory=primary_repo_path,
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

    async def _run_sdk_session(
        self,
        *,
        task_description: str,
        working_directory: Path,
    ) -> str:
        """Launch a Claude Agent SDK session and stream its output.

        Returns the final result text produced by the SDK.
        """
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            UserMessage,
            query,
        )

        result_text = ""
        turn_count = 0

        async for message in query(
            prompt=task_description,
            options=ClaudeAgentOptions(
                allowed_tools=SDK_ALLOWED_TOOLS,
                cwd=str(working_directory),
                max_turns=SDK_MAX_TURNS,
                permission_mode=SDK_PERMISSION_MODE,
                model=SDK_MODEL,
            ),
        ):
            if isinstance(message, AssistantMessage):
                turn_count += 1
                self._log_assistant_message(message, turn_count)

            elif isinstance(message, UserMessage):
                self._log_tool_results(message, turn_count)

            elif isinstance(message, ResultMessage):
                result_text = getattr(message, "result", "") or ""
                self.logger.info(
                    "developer.sdk_finished",
                    total_turns=turn_count,
                    cost_usd=getattr(message, "total_cost_usd", 0),
                    result_length=len(result_text),
                )

        return result_text

    def _log_assistant_message(self, message: Any, turn: int) -> None:
        """Log text output and tool calls from an assistant turn."""
        text = getattr(message, "text", "") or ""
        if text:
            self.logger.info("developer.assistant_text", turn=turn, text=text[:200])

        for tool_call in getattr(message, "tool_calls", []) or []:
            tool_name = getattr(tool_call, "name", "unknown")
            tool_input = getattr(tool_call, "input", {}) or {}
            self.logger.info(
                "developer.tool_call",
                turn=turn,
                tool=tool_name,
                detail=self._summarize_tool_call(tool_name, tool_input),
            )

    def _log_tool_results(self, message: Any, turn: int) -> None:
        """Log tool execution results returned to the SDK."""
        for block in getattr(message, "content", []) or []:
            if hasattr(block, "tool_use_id"):
                self.logger.info(
                    "developer.tool_result",
                    turn=turn,
                    is_error=getattr(block, "is_error", False),
                    result_length=len(str(getattr(block, "content", ""))),
                )

    async def _clone_all_repositories(
        self,
        *,
        github_token: str,
        repositories: list[dict[str, Any]],
        workspace_path: Path,
    ) -> list[dict[str, Any]]:
        """Clone all task repositories concurrently into the workspace directory."""
        git_provider = self._git_factory.for_kind(GitProviderKind.GITHUB)

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
    def _summarize_tool_call(tool_name: str, tool_input: dict[str, Any]) -> str:
        """One-line human-readable summary of a tool call for structured logs."""
        match tool_name:
            case "Read":
                file_path = tool_input.get("file_path", "?")
                offset = tool_input.get("offset")
                return f"{file_path}:{offset}" if offset else file_path
            case "Edit":
                file_path = tool_input.get("file_path", "?")
                replaced_chars = len(tool_input.get("old_string", ""))
                return f"{file_path} (replacing {replaced_chars} chars)"
            case "Write":
                file_path = tool_input.get("file_path", "?")
                content_chars = len(tool_input.get("content", ""))
                return f"{file_path} ({content_chars} chars)"
            case "Glob":
                return tool_input.get("pattern", "?")
            case "Grep":
                return f"/{tool_input.get('pattern', '?')}/"
            case "Bash":
                return tool_input.get("command", "?")[:80]
            case "Agent":
                return tool_input.get("prompt", "?")[:80]
            case _:
                return str(tool_input)[:80]

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


