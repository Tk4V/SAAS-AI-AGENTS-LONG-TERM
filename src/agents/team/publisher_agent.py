"""Publisher agent — commits changes, pushes branches, opens PRs via GitHub REST.

Takes the diffs from the Developer agent and publishes them to GitHub.
Local git operations (config, branch, add, commit, push) run as subprocesses.
PR lookup and creation go directly to api.github.com via ``GitHubApiClient``;
the agent does not drive a Claude SDK session for that step. (Earlier we
routed PR creation through the GitHub Copilot MCP, but the SSE handshake
to api.githubcopilot.com silently dropped tools when handed a standard
GitHub OAuth token, so the model fell back to a blocked ``gh`` CLI call.)
Uses Haiku for PR content generation (cheap and fast).
"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from src.agents.base_agent import BaseAgent
from src.agents.prompts.team.publisher_prompts import (
    PR_CONTENT_TEMPLATE,
    SYSTEM_PROMPT as _PUB_SYSTEM_PROMPT,
)
from src.integrations.github import GitHubApiClient, GitHubGitOps
from src.utils.exceptions import PipelineError


class PublisherAgent(BaseAgent):
    """Commits local changes, pushes feature branches, and opens pull requests.

    For each repository that contains diffs the agent configures git identity,
    creates (or checks out) a feature branch, pushes it, generates PR content
    with Haiku, then opens the PR via the GitHub REST API. Existing open PRs
    on the same head branch are detected and reused (the fix-loop force-pushes
    new commits onto the same branch, so we want the PR URL preserved).
    """

    name: ClassVar[str] = "publisher"
    role: ClassVar[str] = "Publisher"

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
        repo_map = {r.get("name", ""): r for r in repos}
        pr_urls: dict[str, str] = {}

        client = GitHubApiClient(user_id=user_id, token_provider=self.token_provider)
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

                await self._git_prepare(repo_path=local_path, branch=branch_name)
                await GitHubGitOps.push_branch(
                    repo_path=local_path, branch=branch_name, token=token, force=True,
                )

                pr_content = await self._generate_pr_content(
                    description=description,
                    repo_name=repo_name,
                    changes=changes,
                    plan_summary=plan.get("summary", ""),
                )

                existing = await client.find_open_pr(
                    coordinates=coordinates, head=branch_name
                )
                if existing:
                    pr_url = existing["url"]
                    self.logger.info(
                        "publisher.pr_reused", pr=pr_url, repo=repo_name
                    )
                else:
                    created = await client.create_pull_request(
                        coordinates=coordinates,
                        title=pr_content.get("title", "Automated changes by Clyde"),
                        body=pr_content.get("body", ""),
                        head=branch_name,
                        base=default_branch,
                    )
                    pr_url = created["url"]
                    self.logger.info(
                        "publisher.pr_created", pr=pr_url, repo=repo_name
                    )

                pr_urls[coordinates.full_name] = pr_url
        finally:
            await client.aclose()

        event = {
            "name": "publisher.completed",
            "agent": self.name,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": {"pr_count": len(pr_urls)},
        }
        return {"pr_urls": pr_urls, "events": [event]}

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
            system=_PUB_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text

        from src.utils.json_parser import LLMJsonParser

        try:
            return LLMJsonParser.parse(text, agent="Publisher")
        except Exception:
            return {"title": f"clyde: {description[:60]}", "body": text}
