"""Release Manager agent — creates branches, commits, pushes, and opens PRs.

For each repository that has diffs, the agent:
1. Resolves the user's GitHub token from the database.
2. Creates a feature branch named `clyde/{task_id[:8]}/{repo_name}`.
3. Stages and commits the changes.
4. Pushes the branch via GitProvider.
5. Opens a pull request with an LLM-generated title and body.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from src.agents.base import BaseAgent
from src.agents.development_team.release_manager.prompts import (
    PR_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
)
from src.common.crypto import TokenCipher
from src.common.exceptions import PipelineError
from src.db.models.project import GitProviderKind
from src.db.queries.user_credential_queries import UserOAuthCredentialRepository
from src.db.session import Database, db
from src.engine.registry import AgentRegistry
from src.tools import toolbox
from src.tools.git.factory import GitProviderFactory
from src.tools.git.provider import RepoCoordinates
from src.tools.llm.gateway import ChatMessage, LLMGateway

if TYPE_CHECKING:
    from src.engine.state import TaskState


class ReleaseManagerAgent(BaseAgent):
    name = "release_manager"
    role = "Release Manager"

    def __init__(
        self,
        *,
        llm: LLMGateway | None = None,
        git_factory: GitProviderFactory | None = None,
        cipher: TokenCipher | None = None,
        database: Database | None = None,
    ) -> None:
        super().__init__()
        self._llm = llm or toolbox.llm
        self._git_factory = git_factory or toolbox.git
        self._cipher = cipher or toolbox.cipher
        self._database = database or db

    async def execute(self, state: "TaskState") -> dict[str, Any]:
        repos = state.get("repos") or []
        diffs = state.get("diffs") or {}
        description = state.get("description") or ""
        context = state.get("context") or {}
        plan = state.get("plan") or {}
        user_id = state.get("user_id")
        task_id = state.get("task_id") or "unknown"

        if not user_id:
            raise PipelineError("Release Manager invoked without a user_id.")
        if not diffs:
            raise PipelineError("Release Manager invoked without any diffs.")

        token = await self.resolve_github_token(user_id=user_id)
        provider = self._git_factory.for_kind(GitProviderKind.GITHUB)

        # Build a lookup from repo name to repo metadata.
        repo_map: dict[str, dict[str, Any]] = {}
        for repo in repos:
            repo_map[repo.get("name", "")] = repo

        pr_urls: dict[str, str] = {}

        for repo_name, changes in diffs.items():
            repo_meta = repo_map.get(repo_name)
            if not repo_meta:
                self.logger.warning(
                    "release_manager.repo_not_found",
                    repo=repo_name,
                )
                continue

            local_path = Path(repo_meta.get("local_path", ""))
            url = repo_meta.get("url", "")
            default_branch = repo_meta.get("default_branch", "main")

            if not local_path.is_dir():
                self.logger.warning(
                    "release_manager.local_path_missing",
                    repo=repo_name,
                    path=str(local_path),
                )
                continue

            coords = provider.parse_repo_url(url)
            branch_name = f"clyde/{task_id[:8]}/{repo_name}"

            # Git operations: create branch, stage, commit.
            await self._git_prepare(
                repo_path=local_path,
                branch=branch_name,
            )

            # Push the branch.
            await provider.push_branch(
                repo_path=local_path,
                branch=branch_name,
                token=token,
            )

            # Generate PR content with the LLM.
            pr_content = await self._generate_pr_content(
                description=description,
                context=context,
                plan=plan,
                repo_name=repo_name,
                changes=changes,
            )

            # Create the pull request.
            pr_info = await provider.create_pull_request(
                coordinates=coords,
                token=token,
                title=pr_content.get("title", f"clyde: {description[:50]}"),
                body=pr_content.get("body", "Automated PR by Clyde."),
                head=branch_name,
                base=default_branch,
            )

            pr_urls[coords.full_name] = pr_info.url
            self.logger.info(
                "release_manager.pr_created",
                repo=coords.full_name,
                pr_url=pr_info.url,
            )

        event = {
            "name": "release_manager.prs_created",
            "agent": self.name,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": {"pr_count": len(pr_urls)},
        }

        return {
            "pr_urls": pr_urls,
            "events": [event],
        }

    @staticmethod
    async def _git_prepare(*, repo_path: Path, branch: str) -> None:
        """Create a branch, stage all changes, and commit."""
        commands = [
            ["git", "config", "user.name", "Clyde AI"],
            ["git", "config", "user.email", "clyde@noreply.clyde.dev"],
            ["git", "checkout", "-b", branch],
            ["git", "add", "-A"],
            ["git", "commit", "-m", f"clyde: automated changes on {branch}"],
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
                raise PipelineError(
                    f"Git command failed: {' '.join(cmd)}",
                    details={"stderr": stderr.decode(errors="replace")[:500]},
                )

    async def _generate_pr_content(
        self,
        *,
        description: str,
        context: dict[str, Any],
        plan: dict[str, Any],
        repo_name: str,
        changes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Ask the LLM to draft a PR title and body."""
        # Extract the plan slice for this repo.
        repo_plan: dict[str, Any] = {}
        for rp in plan.get("repos", []):
            if rp.get("name") == repo_name:
                repo_plan = rp
                break

        changes_summary = json.dumps(
            [{"path": c.get("path"), "action": c.get("action")} for c in changes],
            indent=2,
        )

        user_message = PR_PROMPT_TEMPLATE.format(
            description=description,
            context=json.dumps(context, indent=2),
            repo_plan=json.dumps(repo_plan, indent=2),
            changes=changes_summary,
        )

        response = await self._llm.chat(
            role="release_manager",
            system=SYSTEM_PROMPT,
            messages=[ChatMessage(role="user", content=user_message)],
        )

        self.logger.info(
            "release_manager.llm_response",
            model=response.model,
            tokens=response.usage.total,
        )

        from src.common.json_utils import parse_llm_json
        try:
            return parse_llm_json(response.text, agent="Release Manager")
        except Exception:
            return {
                "title": f"clyde: {description[:60]}",
                "body": response.text,
            }


# Self-registration so autoload picks up this agent.
_logger = structlog.get_logger("clyde.agent.release_manager")
AgentRegistry.instance().register(ReleaseManagerAgent)
_logger.debug("release_manager.registered")
