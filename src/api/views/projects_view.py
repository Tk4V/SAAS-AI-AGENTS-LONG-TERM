"""HTTP views for project and repository management."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.api.dependencies import CurrentUserDep, ProjectServiceDep
from src.api.schemas.common_schemas import Page, PaginationParams
from src.api.schemas.project_schemas import (
    ProjectBranchesResponse,
    ProjectCreate,
    ProjectListItem,
    ProjectRead,
    ProjectRepoCreate,
    ProjectRepoRead,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["Projects"])


class ProjectView:
    """CRUD operations for projects."""

    @staticmethod
    @router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
    async def create(
        payload: ProjectCreate,
        user: CurrentUserDep,
        service: ProjectServiceDep,
    ) -> ProjectRead:
        """Create a new project with optional repository attachments."""
        project = await service.create(user_id=user.id, payload=payload)
        return ProjectRead.from_orm(project)

    @staticmethod
    @router.get("", response_model=Page[ProjectListItem])
    async def list(
        user: CurrentUserDep,
        service: ProjectServiceDep,
        pagination: Annotated[PaginationParams, Depends()],
    ) -> Page[ProjectListItem]:
        """List all projects for the current user."""
        rows, total = await service.list(
            user_id=user.id, offset=pagination.offset, limit=pagination.limit
        )
        return Page[ProjectListItem](
            items=[ProjectListItem.from_orm(project, count) for project, count in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    @staticmethod
    @router.get("/{project_id}", response_model=ProjectRead)
    async def get(
        project_id: UUID,
        user: CurrentUserDep,
        service: ProjectServiceDep,
    ) -> ProjectRead:
        """Fetch a single project with its attached repositories."""
        project = await service.get(user_id=user.id, project_id=project_id)
        return ProjectRead.from_orm(project)

    @staticmethod
    @router.get("/{project_id}/branches", response_model=ProjectBranchesResponse)
    async def list_branches(
        project_id: UUID,
        user: CurrentUserDep,
        service: ProjectServiceDep,
    ) -> ProjectBranchesResponse:
        """Return all branches for every repo attached to the project."""
        return await service.list_branches(user_id=user.id, project_id=project_id)

    @staticmethod
    @router.patch("/{project_id}", response_model=ProjectRead)
    async def update(
        project_id: UUID,
        payload: ProjectUpdate,
        user: CurrentUserDep,
        service: ProjectServiceDep,
    ) -> ProjectRead:
        """Update project name or description."""
        project = await service.update(
            user_id=user.id, project_id=project_id, payload=payload
        )
        return ProjectRead.from_orm(project)

    @staticmethod
    @router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete(
        project_id: UUID,
        user: CurrentUserDep,
        service: ProjectServiceDep,
    ) -> None:
        """Delete a project and all its attached repositories."""
        await service.delete(user_id=user.id, project_id=project_id)


class ProjectRepoView:
    """Attach and detach repositories from projects."""

    @staticmethod
    @router.post(
        "/{project_id}/repos",
        response_model=ProjectRepoRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def attach(
        project_id: UUID,
        payload: ProjectRepoCreate,
        user: CurrentUserDep,
        service: ProjectServiceDep,
    ) -> ProjectRepoRead:
        """Attach a git repository to a project."""
        repo = await service.add_repo(
            user_id=user.id, project_id=project_id, payload=payload
        )
        return ProjectRepoRead.from_orm(repo)

    @staticmethod
    @router.delete(
        "/{project_id}/repos/{repo_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def detach(
        project_id: UUID,
        repo_id: UUID,
        user: CurrentUserDep,
        service: ProjectServiceDep,
    ) -> None:
        """Detach a repository from a project."""
        await service.remove_repo(
            user_id=user.id, project_id=project_id, repo_id=repo_id
        )
