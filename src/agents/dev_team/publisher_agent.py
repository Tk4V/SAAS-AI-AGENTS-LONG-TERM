"""Publisher agent — commits changes, pushes branches, creates PRs via GitHub MCP.

Takes the diffs from the Developer agent and publishes them to GitHub.
Local git operations (config, branch, add, commit, push) run as subprocesses.
PR creation goes through the GitHub MCP tool (mcp__github__create_pull_request).
Uses Haiku for PR content generation (cheap and fast).
"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from src.agents.prompts.dev_team.publisher_prompts import (
    PR_CONTENT_TEMPLATE,
    SYSTEM_PROMPT,
)
from src.agents.sdk_agent import SDKAgent
from src.integrations.github import GitHubApiClient, GitHubGitOps, RepoCoordinates
from src.utils.exceptions import PipelineError


class PublisherAgent(SDKAgent):
    """Commits local changes, pushes feature branches, and opens pull requests via GitHub MCP.

    For each repository that contains diffs the agent configures git identity,
    creates (or checks out) a feature branch, pushes it, generates PR content
    with Haiku, then delegates PR creation to the GitHub MCP tool.
    """

    name: ClassVar[str] = "publisher"
    role: ClassVar[str] = "Publisher"

    SDK_ALLOWED_TOOLS: ClassVar[list[str]] = ["mcp__github__*"]
    SDK_MODEL: ClassVar[str] = "claude-haiku-4-5"
    SDK_MAX_TURNS: ClassVar[int] = 50
    SDK_PERMISSION_MODE: ClassVar[str] = "acceptEdits"

    async def build_mcp_servers(self, context: dict[str, Any]) -> dict[str, Any]:
        """Mount GitHub MCP server for the user."""
        return await self.build_user_mcp_servers(user_id=context["user_id"])

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Publish diffs as PRs and return the resulting PR URLs."""
        repos = state.get("repos") or []
        diffs = state.get("diffs") or {}
        description = state.get("description") or ""
        plan = state.get("plan") or {}
        user_id = state.get("user_id")
        task_id = state.get("task_id") or "unknown"

        if not user_id:
            raise PipelineError("Publisher invoked without a user_id.")
        if not diffs:
            self.logger.warning("publisher.no_diffs")
            return {"pr_urls": {}, "events": []}

        token = await self.resolve_github_token(user_id=user_id)
        api = GitHubApiClient(
            user_id=user_id,
            token_resolver=self.token_resolver,
            http_client=self.clients.http,
        )

        repo_map = {r.get("name", ""): r for r in repos}
        pr_urls: dict[str, str] = {}

        try:
            for repo_name, changes in diffs.items():
                repo_meta = repo_map.get(repo_name)
                if not repo_meta:
                    continue

                local_path = Path(repo_meta.get("local_path", ""))
                url = repo_meta.get("url", "")
                default_branch = repo_meta.get("default_branch", "main")
                if not local_path.is_dir():
                    continue

                coordinates = GitHubGitOps.parse_repo_url(url)
                branch_name = f"clyde/{task_id[:8]}/{repo_name}"

                # Local git ops: config, branch, stage, commit — must stay as subprocesses
                await self._git_prepare(repo_path=local_path, branch=branch_name)
                await GitHubGitOps.push_branch(
                    repo_path=local_path, branch=branch_name, token=token
                )

                # Generate PR content with Haiku LLM
                pr_content = await self._generate_pr_content(
                    description=description,
                    repo_name=repo_name,
                    changes=changes,
                    plan_summary=plan.get("summary", ""),
                )

                # Skip PR creation if one already exists for this branch
                existing = await api.find_open_pr(
                    coordinates=coordinates, head=branch_name
                )
                if existing:
                    pr_urls[coordinates.full_name] = existing["url"]
                    self.logger.info("publisher.pr_already_exists", pr=existing["url"])
                    continue

                # Create PR via GitHub MCP tool
                pr_prompt = self._build_pr_prompt(
                    coordinates=coordinates,
                    pr_content=pr_content,
                    branch_name=branch_name,
                    default_branch=default_branch,
                )
                await self.run_sdk_session(
                    prompt=pr_prompt,
                    working_directory=local_path,
                    mcp_context={"user_id": user_id},
                )

                # Retrieve PR URL via REST after the SDK session created it
                created = await api.find_open_pr(
                    coordinates=coordinates, head=branch_name
                )
                if created:
                    pr_urls[coordinates.full_name] = created["url"]
                    self.logger.info("publisher.pr_created", pr=created["url"])
        finally:
            await api.aclose()

        event = {
            "name": "publisher.completed",
            "agent": self.name,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": {"pr_count": len(pr_urls)},
        }
        return {"pr_urls": pr_urls, "events": [event]}

    @staticmethod
    def _build_pr_prompt(
        *,
        coordinates: RepoCoordinates,
        pr_content: dict[str, Any],
        branch_name: str,
        default_branch: str,
    ) -> str:
        """Build the SDK session prompt that instructs Haiku to create the PR via MCP."""
        title = pr_content.get("title", "Automated changes by Clyde")
        body = pr_content.get("body", "")
        return (
            f"Create a pull request on GitHub for repository {coordinates.full_name}.\n\n"
            f"Head branch: {branch_name}\n"
            f"Base branch: {default_branch}\n"
            f"Title: {title}\n\n"
            f"Body:\n{body}\n\n"
            "Use the mcp__github__create_pull_request tool to create this PR now."
        )

    @staticmethod
    async def _git_prepare(*, repo_path: Path, branch: str) -> None:
        """Stage, commit, and prepare a feature branch in the local repo.

        Configures the git user, creates or switches to the target branch,
        stages all changes, and commits. Commit failures are tolerated
        because there may be nothing new to commit.
        """

        async def _run_git_command(
            command: list[str], allow_fail: bool = False
        ) -> int:
            """Execute a single git subprocess and return the exit code."""
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(repo_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _, stderr_bytes = await process.communicate()
            if process.returncode != 0 and not allow_fail:
                raise PipelineError(
                    f"Git failed: {' '.join(command)}",
                    details={
                        "stderr": stderr_bytes.decode(errors="replace")[:500]
                    },
                )
            return process.returncode

        await _run_git_command(["git", "config", "user.name", "Clyde AI"])
        await _run_git_command(["git", "config", "user.email", "clyde@noreply.clyde.dev"])
        return_code = await _run_git_command(
            ["git", "checkout", "-b", branch], allow_fail=True
        )
        if return_code != 0:
            await _run_git_command(["git", "checkout", branch])
        await _run_git_command(["git", "add", "-A"])
        await _run_git_command(
            ["git", "commit", "-m", f"clyde: automated changes on {branch}"],
            allow_fail=True,
        )

    async def _generate_pr_content(
        self,
        *,
        description: str,
        repo_name: str,
        changes: list[dict[str, Any]],
        plan_summary: str,
    ) -> dict[str, Any]:
        """Ask the LLM to produce a PR title and markdown body from the change set."""
        changes_summary = "\n".join(
            f"- {change.get('action', 'modify')}: {change.get('path', '?')}"
            for change in changes
        )
        prompt = PR_CONTENT_TEMPLATE.format(
            description=description,
            repo_name=repo_name,
            changes_summary=changes_summary,
            plan_summary=plan_summary,
        )

        response = await self.clients.anthropic.messages.create(
            model=self.ctx.settings.anthropic_model_haiku,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text

        from src.utils.json_parser import LLMJsonParser

        try:
            return LLMJsonParser.parse(text, agent="Publisher")
        except Exception:
            return {"title": f"clyde: {description[:60]}", "body": text}
