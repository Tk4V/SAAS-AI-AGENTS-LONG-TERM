"""Pydantic schemas for the projects and project_repos resources."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from src.db.models.project import ProviderKind

if TYPE_CHECKING:
    from src.db.models.project import Project, ProjectRepo


class ProjectRepoBase(BaseModel):
    provider: ProviderKind = ProviderKind.GITHUB
    url: HttpUrl
    default_branch: str = Field(default="main", min_length=1, max_length=255)


class ProjectRepoCreate(ProjectRepoBase):
    """Payload for attaching a repository to a project.

    OAuth tokens are not accepted here: they are obtained through a dedicated
    OAuth callback endpoint and stored encrypted at rest.
    """


class ProjectRepoRead(ProjectRepoBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, repo: "ProjectRepo") -> "ProjectRepoRead":
        return cls(
            id=repo.id,
            provider=repo.provider,
            url=repo.url,  # type: ignore[arg-type]
            default_branch=repo.default_branch,
            created_at=repo.created_at,
            updated_at=repo.updated_at,
        )


class ProjectBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class ProjectCreate(ProjectBase):
    repos: list[ProjectRepoCreate] = Field(
        default_factory=list,
        description="Optional repositories to attach during project creation.",
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return value.strip()


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repos: list[ProjectRepoRead]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, project: "Project") -> "ProjectRead":
        return cls(
            id=project.id,
            name=project.name,
            description=project.description,
            repos=[ProjectRepoRead.from_orm(repo) for repo in project.repos],
            created_at=project.created_at,
            updated_at=project.updated_at,
        )


class RepoBranches(BaseModel):
    repo_id: UUID
    url: str
    branches: list[str]


class ProjectBranchesResponse(BaseModel):
    repos: list[RepoBranches]


class ProjectListItem(ProjectBase):
    """Compact view used in list endpoints; omits nested repos for speed."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repo_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, project: "Project", repo_count: int) -> "ProjectListItem":
        return cls(
            id=project.id,
            name=project.name,
            description=project.description,
            repo_count=repo_count,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
