"""DevOps Engineer agent — diagnoses CI failures and applies fixes.

When a GitHub Actions workflow fails, the webhook handler constructs a minimal
state and calls this agent directly (outside the LangGraph pipeline). The agent:

1. Fetches the CI logs from GitHub via GitProvider.
2. Reads the recently changed files from the local clone.
3. Asks the LLM to diagnose the failure and produce file-level fixes.
4. Applies the fixes to disk.
5. Stages, commits, and pushes to the same branch — triggering CI again.

The agent does NOT create new PRs or branches. It pushes to the existing
feature branch so the open PR picks up the fix automatically.
"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from src.agents.base import BaseAgent
from src.agents.development_team.devops_engineer.prompts import (
    FIX_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
)
from src.agents.development_team.senior_developer.parsers import DiffParser
from src.common.crypto import TokenCipher
from src.common.exceptions import PipelineError
from src.db.models.project import GitProviderKind
from src.db.queries.user_credential_queries import UserOAuthCredentialRepository
from src.db.session import Database, db
from src.engine.registry import AgentRegistry
from src.tools import toolbox
from src.tools.git.factory import GitProviderFactory
from src.tools.llm.gateway import ChatMessage, LLMGateway

if TYPE_CHECKING:
    from src.engine.state import TaskState


class DevOpsEngineerAgent(BaseAgent):
    name = "devops_engineer"
    role = "DevOps Engineer"

    def __init__(
        self,
        *,
        llm: LLMGateway | None = None,
        git_factory: GitProviderFactory | None = None,
        cipher: TokenCipher | None = None,
        database: Database | None = None,
        parser: DiffParser | None = None,
    ) -> None:
        super().__init__()
        self._llm = llm or toolbox.llm
        self._git_factory = git_factory or toolbox.git
        self._cipher = cipher or toolbox.cipher
        self._database = database or db
        self._parser = parser or DiffParser()

    async def execute(self, state: "TaskState") -> dict[str, Any]:
        user_id = state.get("user_id")
        repos = state.get("repos") or []
        diffs = state.get("diffs") or {}
        description = state.get("description") or ""
        attempt = int(state.get("attempt") or 0)
        max_attempts = int(state.get("max_fix_attempts") or 3)
        ci_run_id = state.get("ci_run_id")
        ci_repo_full_name = state.get("ci_repo_full_name")

        if not user_id:
            raise PipelineError("DevOps Engineer invoked without a user_id.")
        if not repos:
            raise PipelineError("DevOps Engineer invoked without repositories.")
        if not ci_run_id or not ci_repo_full_name:
            raise PipelineError("DevOps Engineer invoked without CI run context.")

        token = await self.resolve_github_token(user_id=user_id)
        provider = self._git_factory.for_kind(GitProviderKind.GITHUB)

        # Figure out which repo the CI run belongs to.
        coords = provider.parse_repo_url(f"https://github.com/{ci_repo_full_name}")

        ci_logs = await provider.fetch_workflow_run_logs(
            coordinates=coords,
            token=token,
            run_id=ci_run_id,
        )
        # Trim absurdly long logs so we don't blow out the context window.
        if len(ci_logs) > 50_000:
            ci_logs = ci_logs[:25_000] + "\n\n... (truncated) ...\n\n" + ci_logs[-25_000:]

        # Build a map of repo name -> local path and read the changed files.
        repo_map: dict[str, dict[str, Any]] = {}
        for repo in repos:
            repo_map[repo.get("name", "")] = repo

        changed_files_text = self._read_changed_files(diffs=diffs, repo_map=repo_map)

        # Ask the LLM to diagnose and fix.
        raw_response = await self._generate_fix(
            description=description,
            ci_logs=ci_logs,
            changed_files=changed_files_text,
            attempt=attempt + 1,
            max_attempts=max_attempts,
        )

        parsed = self._parser.parse(raw_response)
        if not parsed:
            raise PipelineError(
                "DevOps Engineer LLM produced no file changes.",
                details={"raw": raw_response[:500]},
            )

        # Determine repo paths for writing fixes.
        repo_paths: dict[str, Path] = {}
        for repo in repos:
            name = repo.get("name", "")
            local = repo.get("local_path", "")
            if name and local:
                repo_paths[name] = Path(local)

        # Apply changes, commit, and push.
        updated_diffs: dict[str, list[dict[str, Any]]] = dict(diffs)
        files_fixed = 0

        for change in parsed:
            repo_name = self._resolve_repo(change.path, repo_paths, ci_repo_full_name)
            repo_path = repo_paths.get(repo_name)
            if repo_path is None:
                self.logger.warning(
                    "devops_engineer.unknown_repo",
                    path=change.path,
                )
                continue

            self._apply_change(
                repo_path=repo_path,
                file_path=change.path,
                action=change.action,
                content=change.content,
            )
            updated_diffs.setdefault(repo_name, []).append(
                {
                    "path": change.path,
                    "action": change.action,
                    "content": change.content,
                }
            )
            files_fixed += 1

        # Commit and push to the same branch for each affected repo.
        for repo in repos:
            repo_name = repo.get("name", "")
            local_path = repo.get("local_path", "")
            branch = repo.get("branch", "")
            if not local_path or not branch:
                continue

            repo_path = Path(local_path)
            if not repo_path.is_dir():
                continue

            await self._git_commit_and_push(
                repo_path=repo_path,
                branch=branch,
                token=token,
                provider=provider,
                attempt=attempt + 1,
            )

        event = {
            "name": "devops_engineer.fix_applied",
            "agent": self.name,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": {
                "attempt": attempt + 1,
                "files_fixed": files_fixed,
                "ci_run_id": ci_run_id,
            },
        }

        return {
            "diffs": updated_diffs,
            "attempt": attempt + 1,
            "events": [event],
        }

    def _read_changed_files(
        self,
        *,
        diffs: dict[str, list[dict[str, Any]]],
        repo_map: dict[str, dict[str, Any]],
    ) -> str:
        """Read the current on-disk content of files that were changed by Senior Dev."""
        sections: list[str] = []
        for repo_name, changes in diffs.items():
            repo_meta = repo_map.get(repo_name, {})
            local_path = repo_meta.get("local_path", "")
            if not local_path:
                continue
            base = Path(local_path)
            for change in changes:
                file_rel = change.get("path", "")
                full_path = base / file_rel
                if full_path.is_file():
                    try:
                        content = full_path.read_text(encoding="utf-8", errors="replace")
                        sections.append(
                            f"# repo: {repo_name} | file: {file_rel}\n{content}"
                        )
                    except OSError:
                        self.logger.warning(
                            "devops_engineer.file_read_failed",
                            repo=repo_name,
                            file=file_rel,
                        )
        return "\n\n".join(sections) if sections else "(no changed files found on disk)"

    async def _generate_fix(
        self,
        *,
        description: str,
        ci_logs: str,
        changed_files: str,
        attempt: int,
        max_attempts: int,
    ) -> str:
        """Call the LLM to diagnose the CI failure and produce a fix."""
        user_message = FIX_PROMPT_TEMPLATE.format(
            description=description,
            ci_logs=ci_logs,
            changed_files=changed_files,
            attempt=attempt,
            max_attempts=max_attempts,
            extra_context="",
        )

        response = await self._llm.chat(
            role="devops_engineer",
            system=SYSTEM_PROMPT,
            messages=[ChatMessage(role="user", content=user_message)],
        )

        self.logger.info(
            "devops_engineer.llm_response",
            model=response.model,
            tokens=response.usage.total,
        )
        return response.text

    @staticmethod
    def _resolve_repo(
        file_path: str,
        repo_paths: dict[str, Path],
        ci_repo_full_name: str,
    ) -> str:
        """Figure out which repo a file belongs to.

        For CI fixes we usually know which repo failed, so we check if the
        repo's short name matches. Falls back to the first repo if there's
        only one.
        """
        # The CI repo full_name is "owner/name"; extract the short name.
        ci_short = ci_repo_full_name.rsplit("/", 1)[-1] if ci_repo_full_name else ""
        if ci_short in repo_paths:
            return ci_short

        repo_names = list(repo_paths.keys())
        return repo_names[0] if repo_names else ""

    @staticmethod
    def _apply_change(
        *,
        repo_path: Path,
        file_path: str,
        action: str,
        content: str,
    ) -> None:
        """Write the fix to disk."""
        target = repo_path / file_path
        if action == "delete":
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content + "\n", encoding="utf-8")

    async def _git_commit_and_push(
        self,
        *,
        repo_path: Path,
        branch: str,
        token: str,
        provider: Any,
        attempt: int,
    ) -> None:
        """Stage all changes, commit with a descriptive message, and push."""
        commands = [
            ["git", "add", "-A"],
            ["git", "commit", "-m", f"clyde: CI fix attempt #{attempt}"],
        ]
        for cmd in commands:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(repo_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _stdout, stderr = await process.communicate()
            if process.returncode != 0:
                # git commit returns 1 when there's nothing to commit, which
                # is fine — the push will be a no-op.
                if cmd[1] == "commit":
                    self.logger.info(
                        "devops_engineer.nothing_to_commit",
                        repo=str(repo_path),
                    )
                    return
                raise PipelineError(
                    f"Git command failed: {' '.join(cmd)}",
                    details={"stderr": stderr.decode(errors="replace")[:500]},
                )

        await provider.push_branch(
            repo_path=repo_path,
            branch=branch,
            token=token,
        )
        self.logger.info("devops_engineer.pushed", branch=branch)


# Self-registration so autoload picks up this agent.
_logger = structlog.get_logger("clyde.agent.devops_engineer")
AgentRegistry.instance().register(DevOpsEngineerAgent)
_logger.debug("devops_engineer.registered")
