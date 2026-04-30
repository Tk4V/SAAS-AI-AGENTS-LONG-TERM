"""Project service: orchestrates project repository calls and cross-cutting rules.

Branch listing dispatches by repo provider to the matching API client. Today
only GitHub is wired; GitLab/Bitbucket land here when their API clients do.
"""

from __future__ import annotations

from uuid import UUID

from src.api.schemas.project_schemas import (
    ProjectBranchesResponse,
    ProjectCreate,
    ProjectRepoCreate,
    ProjectUpdate,
    RepoBranches,
)
from src.credentials.oauth.token_provider import OAuthTokenProvider
from src.db.models.project import Project, ProjectRepo, ProviderKind
from src.db.queries.project_query import ProjectRepository
from src.integrations.github import GitHubApiClient
from src.integrations.github.git_ops import GitHubGitOps


class ProjectService:
    def __init__(
        self,
        repository: ProjectRepository,
        oauth_token_provider: OAuthTokenProvider,
    ) -> None:
        self._repo = repository
        self._token_provider = oauth_token_provider

    async def create(self, *, user_id: int, payload: ProjectCreate) -> Project:
        project = await self._repo.create(
            user_id=user_id,
            name=payload.name,
            description=payload.description,
        )
        for repo in payload.repos:
            await self._repo.add_repo(
                user_id=user_id,
                project_id=project.id,
                provider=repo.provider,
                url=str(repo.url),
                default_branch=repo.default_branch,
            )
        if payload.repos:
            project = await self._repo.get(user_id=user_id, project_id=project.id)
        return project

    async def get(self, *, user_id: int, project_id: UUID) -> Project:
        return await self._repo.get(user_id=user_id, project_id=project_id)

    async def list_branches(
        self,
        *,
        user_id: int,
        project_id: UUID,
    ) -> ProjectBranchesResponse:
        project = await self._repo.get(user_id=user_id, project_id=project_id)
        result: list[RepoBranches] = []
        for repo in project.repos:
            branches = await self._list_branches_for_repo(
                user_id=user_id, repo=repo
            )
            result.append(
                RepoBranches(repo_id=repo.id, url=repo.url, branches=branches)
            )
        return ProjectBranchesResponse(repos=result)

    async def _list_branches_for_repo(
        self,
        *,
        user_id: int,
        repo: ProjectRepo,
    ) -> list[str]:
        if repo.provider is not ProviderKind.GITHUB:
            raise NotImplementedError(
                f"list_branches not wired for {repo.provider.value}."
            )
        coordinates = GitHubGitOps.parse_repo_url(repo.url)
        client = GitHubApiClient(
            user_id=user_id, token_provider=self._token_provider
        )
        try:
            return await client.list_branches(coordinates=coordinates)
        finally:
            await client.aclose()

    async def list(
        self,
        *,
        user_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[tuple[Project, int]], int]:
        return await self._repo.list(user_id=user_id, offset=offset, limit=limit)

    async def update(
        self,
        *,
        user_id: int,
        project_id: UUID,
        payload: ProjectUpdate,
    ) -> Project:
        await self._repo.update(
            user_id=user_id,
            project_id=project_id,
            name=payload.name,
            description=payload.description,
        )
        return await self._repo.get(user_id=user_id, project_id=project_id)

    async def delete(self, *, user_id: int, project_id: UUID) -> None:
        await self._repo.delete(user_id=user_id, project_id=project_id)

    async def add_repo(
        self,
        *,
        user_id: int,
        project_id: UUID,
        payload: ProjectRepoCreate,
    ) -> ProjectRepo:
        return await self._repo.add_repo(
            user_id=user_id,
            project_id=project_id,
            provider=payload.provider,
            url=str(payload.url),
            default_branch=payload.default_branch,
        )

    async def remove_repo(
        self,
        *,
        user_id: int,
        project_id: UUID,
        repo_id: UUID,
    ) -> None:
        await self._repo.remove_repo(
            user_id=user_id, project_id=project_id, repo_id=repo_id
        )
