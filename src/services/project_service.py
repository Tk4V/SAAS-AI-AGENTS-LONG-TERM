"""Project service: orchestrates project repository calls and cross-cutting rules.

The service stays thin in M1; later steps will add token encryption, GitProvider
verification of repo URLs, and audit logging here.
"""

from __future__ import annotations

from uuid import UUID

from src.api.schemas.project_schemas import (
    ProjectCreate,
    ProjectRepoCreate,
    ProjectUpdate,
)
from src.db.models.project import Project, ProjectRepo
from src.db.queries.project_query import ProjectRepository


class ProjectService:
    def __init__(self, repository: ProjectRepository) -> None:
        self._repo = repository

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
