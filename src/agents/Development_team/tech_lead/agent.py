"""Tech Lead agent — first node in the development pipeline.

Responsibilities:
1. Resolve the user's GitHub OAuth token from the database (encrypted at rest).
2. Shallow-clone every attached repository into a task-scoped temp directory.
3. Walk each clone with `RepoScanner` to produce a budget-aware snapshot.
4. Hand the snapshots to `MultiRepoContextMerger` for a single LLM call that
   produces a unified cross-repo context.
5. Return the cloned repo metadata, the merged context, and a pipeline event
   describing what happened.

Tokens never enter the LangGraph state, so they are not persisted by the
checkpointer. They live only inside the `execute` call.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from src.agents.base import BaseAgent
from src.agents.development_team.tech_lead.multi_repo_context_merger import (
    MultiRepoContextMerger,
)
from src.agents.development_team.tech_lead.repo_scanner import RepoInsight, RepoScanner
from src.common.crypto import TokenCipher
from src.common.exceptions import PipelineError
from src.db.models.project import GitProviderKind
from src.db.queries.user_credential_queries import UserOAuthCredentialRepository
from src.db.session import Database, db
from src.engine.registry import AgentRegistry
from src.tools import toolbox
from src.tools.git.factory import GitProviderFactory
from src.tools.llm.gateway import LLMGateway

if TYPE_CHECKING:
    from src.engine.state import TaskState


class TechLeadAgent(BaseAgent):
    name = "tech_lead"
    role = "Tech Lead"

    def __init__(
        self,
        *,
        llm: LLMGateway | None = None,
        git_factory: GitProviderFactory | None = None,
        cipher: TokenCipher | None = None,
        database: Database | None = None,
        scanner: RepoScanner | None = None,
        merger: MultiRepoContextMerger | None = None,
    ) -> None:
        super().__init__()
        self._llm = llm or toolbox.llm
        self._git_factory = git_factory or toolbox.git
        self._cipher = cipher or toolbox.cipher
        self._database = database or db
        self._scanner = scanner or RepoScanner()
        self._merger = merger or MultiRepoContextMerger(self._llm)

    async def execute(self, state: "TaskState") -> dict[str, Any]:
        task_description = state.get("description") or ""
        user_id = state.get("user_id")
        repos = state.get("repos") or []
        task_id = state.get("task_id") or "unknown"

        if not user_id:
            raise PipelineError("Tech Lead invoked without a user_id in state.")
        if not repos:
            raise PipelineError("Tech Lead invoked without any repositories.")

        token = await self.resolve_github_token(user_id=user_id)
        workspace = self._make_workspace(task_id=task_id)

        cloned_repos = await self._clone_all(token=token, repos=repos, workspace=workspace)
        insights = self._scan_all(cloned_repos)
        context = await self._merger.merge(task=task_description, insights=insights)

        event = {
            "name": "tech_lead.context_built",
            "agent": self.name,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": {
                "repo_count": len(cloned_repos),
                "files_returned": sum(len(insight.snippets) for insight in insights),
            },
        }

        return {
            "repos": cloned_repos,
            "context": context,
            "events": [event],
        }

    @staticmethod
    def _make_workspace(*, task_id: str) -> Path:
        path = Path(tempfile.mkdtemp(prefix=f"clyde_task_{task_id}_"))
        return path

    async def _clone_all(
        self,
        *,
        token: str,
        repos: list[dict[str, Any]],
        workspace: Path,
    ) -> list[dict[str, Any]]:
        provider = self._git_factory.for_kind(GitProviderKind.GITHUB)
        cloned: list[dict[str, Any]] = []
        for repo in repos:
            url = repo.get("url")
            if not url:
                raise PipelineError(
                    "Repository entry is missing a url.",
                    details={"repo": repo},
                )
            coords = provider.parse_repo_url(url)
            destination = workspace / coords.name
            branch = repo.get("default_branch") or "main"

            cloned_repo = await provider.clone(
                coordinates=coords,
                token=token,
                branch=branch,
                destination=destination,
            )
            cloned.append(
                {
                    "name": coords.name,
                    "url": url,
                    "default_branch": branch,
                    "local_path": str(cloned_repo.local_path),
                    "branch": cloned_repo.branch,
                    "head_commit": cloned_repo.head_commit,
                }
            )
        return cloned

    def _scan_all(self, cloned_repos: list[dict[str, Any]]) -> list[RepoInsight]:
        insights: list[RepoInsight] = []
        for repo in cloned_repos:
            insight = self._scanner.scan(
                name=repo["name"],
                root=Path(repo["local_path"]),
            )
            insights.append(insight)
        return insights


# Self-registration: importing this module via AgentRegistry.autoload() makes
# the agent available to PipelineGraphBuilder.
_logger = structlog.get_logger("clyde.agent.tech_lead")
AgentRegistry.instance().register(TechLeadAgent)
_logger.debug("tech_lead.registered")
