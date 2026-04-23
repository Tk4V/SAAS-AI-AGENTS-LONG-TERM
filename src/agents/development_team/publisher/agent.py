"""Publisher agent — commits changes, pushes branches, creates PRs.

Takes the diffs from the Developer agent and publishes them to GitHub.
Uses Haiku for PR content generation (cheap and fast).
"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from src.agents.base import BaseAgent
from src.common.exceptions import PipelineError
from src.db.models.project import GitProviderKind
from src.engine.prompt_assembler import PromptAssembler
from src.engine.registry import AgentRegistry
from src.tools import toolbox
from src.tools.git.factory import GitProviderFactory
from src.tools.llm.gateway import ChatMessage, LLMGateway

if TYPE_CHECKING:
    from src.engine.state import TaskState

PR_PROMPT = """\
Task: {description}
Repository: {repo_name}

Changes made:
{changes_summary}

Plan summary: {plan_summary}

Generate a pull request title and body as JSON:
{{
  "title": "<concise title, max 72 chars>",
  "body": "<markdown body: ## Summary, ## Changes, ## Testing>"
}}

Reply with JSON only.
"""


class PublisherAgent(BaseAgent):
    name = "publisher"
    role = "Publisher"

    def __init__(self, *, llm: LLMGateway | None = None, git_factory: GitProviderFactory | None = None) -> None:
        super().__init__()
        self._llm = llm or toolbox.llm
        self._git_factory = git_factory or toolbox.git

    async def execute(self, state: "TaskState") -> dict[str, Any]:
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
        provider = self._git_factory.for_kind(GitProviderKind.GITHUB)

        repo_map = {r.get("name", ""): r for r in repos}
        pr_urls: dict[str, str] = {}

        for repo_name, changes in diffs.items():
            repo_meta = repo_map.get(repo_name)
            if not repo_meta:
                continue

            local_path = Path(repo_meta.get("local_path", ""))
            url = repo_meta.get("url", "")
            default_branch = repo_meta.get("default_branch", "main")
            if not local_path.is_dir():
                continue

            coords = provider.parse_repo_url(url)
            branch_name = f"clyde/{task_id[:8]}/{repo_name}"

            await self._git_prepare(repo_path=local_path, branch=branch_name)
            await provider.push_branch(repo_path=local_path, branch=branch_name, token=token)

            pr_content = await self._generate_pr_content(
                description=description, repo_name=repo_name,
                changes=changes, plan_summary=plan.get("summary", ""),
            )

            existing = await provider.find_open_pr(coordinates=coords, token=token, head=branch_name)
            if existing:
                pr_urls[coords.full_name] = existing.url
            else:
                pr_info = await provider.create_pull_request(
                    coordinates=coords, token=token,
                    title=pr_content.get("title", f"clyde: {description[:50]}"),
                    body=pr_content.get("body", "Automated PR by Clyde."),
                    head=branch_name, base=default_branch,
                )
                pr_urls[coords.full_name] = pr_info.url
                self.logger.info("publisher.pr_created", pr=pr_info.url)

        event = {
            "name": "publisher.completed",
            "agent": self.name,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": {"pr_count": len(pr_urls)},
        }

        return {"pr_urls": pr_urls, "events": [event]}

    @staticmethod
    async def _git_prepare(*, repo_path: Path, branch: str) -> None:
        async def _run(cmd: list[str], allow_fail: bool = False) -> int:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(repo_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode != 0 and not allow_fail:
                raise PipelineError(f"Git failed: {' '.join(cmd)}", details={"stderr": err.decode(errors="replace")[:500]})
            return proc.returncode

        await _run(["git", "config", "user.name", "Clyde AI"])
        await _run(["git", "config", "user.email", "clyde@noreply.clyde.dev"])
        rc = await _run(["git", "checkout", "-b", branch], allow_fail=True)
        if rc != 0:
            await _run(["git", "checkout", branch])
        await _run(["git", "add", "-A"])
        await _run(["git", "commit", "-m", f"clyde: automated changes on {branch}"], allow_fail=True)

    async def _generate_pr_content(self, *, description: str, repo_name: str, changes: list[dict[str, Any]], plan_summary: str) -> dict[str, Any]:
        changes_summary = "\n".join(f"- {c.get('action', 'modify')}: {c.get('path', '?')}" for c in changes)
        prompt = PR_PROMPT.format(description=description, repo_name=repo_name, changes_summary=changes_summary, plan_summary=plan_summary)
        system = await PromptAssembler.for_role("publisher")
        response = await self._llm.chat(role="publisher", system=system, messages=[ChatMessage(role="user", content=prompt)], max_tokens=2048)
        from src.common.json_utils import parse_llm_json
        try:
            return parse_llm_json(response.text, agent="Publisher")
        except Exception:
            return {"title": f"clyde: {description[:60]}", "body": response.text}


_logger = structlog.get_logger("clyde.agent.publisher")
AgentRegistry.instance().register(PublisherAgent)
_logger.debug("publisher.registered")
