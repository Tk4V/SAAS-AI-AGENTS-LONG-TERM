"""HTTP routes for projects and their attached repositories."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.api.deps import CurrentUserDep, ProjectServiceDep
from src.api.schemas.common_schemas import Page, PaginationParams
from src.api.schemas.project_schemas import (
    ProjectCreate,
    ProjectListItem,
    ProjectRead,
    ProjectRepoCreate,
    ProjectRepoRead,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    payload: ProjectCreate,
    user: CurrentUserDep,
    service: ProjectServiceDep,
) -> ProjectRead:
    project = await service.create(user_id=user.id, payload=payload)
    return ProjectRead.from_orm(project)


@router.get("", response_model=Page[ProjectListItem])
async def list_projects(
    user: CurrentUserDep,
    service: ProjectServiceDep,
    pagination: Annotated[PaginationParams, Depends()],
) -> Page[ProjectListItem]:
    rows, total = await service.list(
        user_id=user.id, offset=pagination.offset, limit=pagination.limit
    )
    return Page[ProjectListItem](
        items=[ProjectListItem.from_orm(project, count) for project, count in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: UUID,
    user: CurrentUserDep,
    service: ProjectServiceDep,
) -> ProjectRead:
    project = await service.get(user_id=user.id, project_id=project_id)
    return ProjectRead.from_orm(project)


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    user: CurrentUserDep,
    service: ProjectServiceDep,
) -> ProjectRead:
    project = await service.update(
        user_id=user.id, project_id=project_id, payload=payload
    )
    return ProjectRead.from_orm(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    user: CurrentUserDep,
    service: ProjectServiceDep,
) -> None:
    await service.delete(user_id=user.id, project_id=project_id)


@router.post(
    "/{project_id}/repos",
    response_model=ProjectRepoRead,
    status_code=status.HTTP_201_CREATED,
)
async def attach_repo(
    project_id: UUID,
    payload: ProjectRepoCreate,
    user: CurrentUserDep,
    service: ProjectServiceDep,
) -> ProjectRepoRead:
    repo = await service.add_repo(
        user_id=user.id, project_id=project_id, payload=payload
    )
    return ProjectRepoRead.from_orm(repo)


@router.delete(
    "/{project_id}/repos/{repo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def detach_repo(
    project_id: UUID,
    repo_id: UUID,
    user: CurrentUserDep,
    service: ProjectServiceDep,
) -> None:
    await service.remove_repo(
        user_id=user.id, project_id=project_id, repo_id=repo_id
    )
