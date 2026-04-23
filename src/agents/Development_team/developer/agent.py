"""Developer agent — runs Claude Agent SDK to explore, plan, and edit code.

One Agent SDK session does everything Claude Code does: reads files, greps
for patterns, plans changes, makes edits, verifies syntax. We just clone
the repo, point the SDK at it, and collect the results.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from src.agents.base import BaseAgent
from src.common.exceptions import PipelineError
from src.db.models.project import GitProviderKind
from src.db.session import Database, db
from src.engine.registry import AgentRegistry
from src.tools import toolbox
from src.tools.git.factory import GitProviderFactory

if TYPE_CHECKING:
    from src.engine.state import TaskState


class DeveloperAgent(BaseAgent):
    name = "developer"
    role = "Developer"

    def __init__(
        self,
        *,
        git_factory: GitProviderFactory | None = None,
        database: Database | None = None,
    ) -> None:
        super().__init__()
        self._git_factory = git_factory or toolbox.git
        self._database = database or db

    async def execute(self, state: "TaskState") -> dict[str, Any]:
        from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage, UserMessage

        description = state.get("description") or ""
        user_id = state.get("user_id")
        repos = state.get("repos") or []
        task_id = state.get("task_id") or "unknown"

        if not user_id:
            raise PipelineError("Developer invoked without a user_id.")
        if not repos:
            raise PipelineError("Developer invoked without repositories.")

        token = await self.resolve_github_token(user_id=user_id)
        workspace = Path(tempfile.mkdtemp(prefix=f"clyde_task_{task_id}_"))

        cloned_repos = await self._clone_all(token=token, repos=repos, workspace=workspace)

        repo_path = Path(cloned_repos[0]["local_path"])
        repo_name = cloned_repos[0]["name"]

        self.logger.info("developer.starting", repo=repo_name, task=description[:80])

        result_text = ""
        turn = 0
        async for message in query(
            prompt=description,
            options=ClaudeAgentOptions(
                allowed_tools=[
                    "Read", "Edit", "Write",
                    "Glob", "Grep",
                    "Bash(git diff*)",
                    "Bash(python -m py_compile*)",
                    "Agent",
                ],
                cwd=str(repo_path),
                max_turns=50,
                permission_mode="acceptEdits",
                model="claude-sonnet-4-6",
            ),
        ):
            msg_type = type(message).__name__

            if isinstance(message, AssistantMessage):
                turn += 1
                # Log text output
                text = getattr(message, "text", "") or ""
                if text:
                    self.logger.info("developer.assistant", turn=turn, text=text[:200])

                # Log each tool call
                for tc in getattr(message, "tool_calls", []) or []:
                    tool_name = getattr(tc, "name", "?")
                    tool_input = getattr(tc, "input", {}) or {}
                    detail = self._tool_summary(tool_name, tool_input)
                    self.logger.info("developer.tool_call", turn=turn, tool=tool_name, detail=detail)

            elif isinstance(message, UserMessage):
                # Tool results coming back
                for block in getattr(message, "content", []) or []:
                    if hasattr(block, "tool_use_id"):
                        is_err = getattr(block, "is_error", False)
                        content = getattr(block, "content", "") or ""
                        self.logger.info(
                            "developer.tool_result",
                            turn=turn,
                            is_error=is_err,
                            result_len=len(str(content)),
                        )

            elif isinstance(message, ResultMessage):
                result_text = getattr(message, "result", "") or ""
                self.logger.info(
                    "developer.sdk_done",
                    turns=turn,
                    cost_usd=getattr(message, "total_cost_usd", 0),
                    result_len=len(result_text),
                )

            else:
                self.logger.debug("developer.sdk_message", type=msg_type)

        diffs = await self._collect_git_diffs(repo_path, repo_name)

        self.logger.info("developer.done", files_changed=len(diffs.get(repo_name, [])))

        event = {
            "name": "developer.completed",
            "agent": self.name,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": {"files_changed": len(diffs.get(repo_name, []))},
        }

        return {
            "repos": cloned_repos,
            "diffs": diffs,
            "context": {"summary": result_text[:2000]},
            "events": [event],
        }

    @staticmethod
    def _tool_summary(name: str, inp: dict) -> str:
        """One-line summary of a tool call for structured logs."""
        match name:
            case "Read":
                path = inp.get("file_path", "?")
                offset = inp.get("offset")
                return f"{path}:{offset}" if offset else path
            case "Edit":
                return f"{inp.get('file_path', '?')} (replace {len(inp.get('old_string', ''))} chars)"
            case "Write":
                return f"{inp.get('file_path', '?')} ({len(inp.get('content', ''))} chars)"
            case "Glob":
                return inp.get("pattern", "?")
            case "Grep":
                return f"/{inp.get('pattern', '?')}/"
            case "Bash":
                return inp.get("command", "?")[:80]
            case "Agent":
                return inp.get("prompt", "?")[:80]
            case _:
                return str(inp)[:80]

    # ------------------------------------------------------------------
    # Clone + diff
    # ------------------------------------------------------------------

    async def _clone_all(self, *, token: str, repos: list[dict[str, Any]], workspace: Path) -> list[dict[str, Any]]:
        provider = self._git_factory.for_kind(GitProviderKind.GITHUB)

        async def _clone_one(repo: dict[str, Any]) -> dict[str, Any]:
            url = repo.get("url")
            if not url:
                raise PipelineError("Repository missing url.")
            coords = provider.parse_repo_url(url)
            branch = repo.get("default_branch") or "main"
            cloned = await provider.clone(
                coordinates=coords, token=token,
                branch=branch, destination=workspace / coords.name,
            )
            return {
                "name": coords.name, "url": url, "default_branch": branch,
                "local_path": str(cloned.local_path),
                "branch": cloned.branch, "head_commit": cloned.head_commit,
            }

        return await asyncio.gather(*[_clone_one(r) for r in repos])

    @staticmethod
    async def _collect_git_diffs(repo_path: Path, repo_name: str) -> dict[str, list[dict[str, Any]]]:
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--name-status", "HEAD",
            cwd=str(repo_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        proc2 = await asyncio.create_subprocess_exec(
            "git", "ls-files", "--others", "--exclude-standard",
            cwd=str(repo_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout2, _ = await proc2.communicate()

        changes: list[dict[str, Any]] = []
        for line in stdout.decode().strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                action = "create" if parts[0].startswith("A") else "modify"
                changes.append({"path": parts[1], "action": action})
        for line in stdout2.decode().strip().splitlines():
            if line.strip():
                changes.append({"path": line.strip(), "action": "create"})

        return {repo_name: changes} if changes else {}


_logger = structlog.get_logger("clyde.agent.developer")
AgentRegistry.instance().register(DeveloperAgent)
_logger.debug("developer.registered")
