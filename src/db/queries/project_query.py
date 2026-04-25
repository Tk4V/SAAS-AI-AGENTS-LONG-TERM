"""Database access for Project and ProjectRepo.

`ProjectRepository` is constructed with an `AsyncSession` and is the only
component allowed to issue SQL against these tables. Services collaborate with
it through its methods rather than building queries directly.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.utils.exceptions import AlreadyExistsError, NotFoundError
from src.db.models.project import GitProviderKind, Project, ProjectRepo


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        name: str,
        description: str | None,
    ) -> Project:
        project = Project(user_id=user_id, name=name, description=description)
        self._session.add(project)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AlreadyExistsError(
                f"A project named {name!r} already exists for this user.",
            ) from exc
        await self._session.refresh(project, attribute_names=["repos"])
        return project

    async def get(self, *, user_id: int, project_id: UUID) -> Project:
        stmt = (
            select(Project)
            .where(Project.id == project_id, Project.user_id == user_id)
            .options(selectinload(Project.repos))
        )
        project = (await self._session.execute(stmt)).scalar_one_or_none()
        if project is None:
            raise NotFoundError(f"Project {project_id} was not found.")
        return project

    async def list(
        self,
        *,
        user_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[tuple[Project, int]], int]:
        """Return (project, repo_count) pairs for the page and the total row count."""
        repo_count = (
            select(func.count(ProjectRepo.id))
            .where(ProjectRepo.project_id == Project.id)
            .correlate(Project)
            .scalar_subquery()
        )

        stmt = (
            select(Project, repo_count.label("repo_count"))
            .where(Project.user_id == user_id)
            .order_by(Project.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        total = await self._session.scalar(
            select(func.count(Project.id)).where(Project.user_id == user_id)
        )
        return [(row.Project, row.repo_count) for row in rows], int(total or 0)

    async def update(
        self,
        *,
        user_id: int,
        project_id: UUID,
        name: str | None = None,
        description: str | None = None,
    ) -> Project:
        project = await self.get(user_id=user_id, project_id=project_id)

        if name is not None:
            project.name = name
        if description is not None:
            project.description = description

        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AlreadyExistsError(
                f"A project named {project.name!r} already exists for this user.",
            ) from exc
        return project

    async def delete(self, *, user_id: int, project_id: UUID) -> None:
        project = await self.get(user_id=user_id, project_id=project_id)
        await self._session.delete(project)

    async def add_repo(
        self,
        *,
        user_id: int,
        project_id: UUID,
        provider: GitProviderKind,
        url: str,
        default_branch: str,
    ) -> ProjectRepo:
        project = await self.get(user_id=user_id, project_id=project_id)
        repo = ProjectRepo(
            project_id=project.id,
            provider=provider,
            url=url,
            default_branch=default_branch,
        )
        self._session.add(repo)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AlreadyExistsError(
                f"Repository {url} is already attached to this project.",
            ) from exc
        return repo

    async def remove_repo(
        self,
        *,
        user_id: int,
        project_id: UUID,
        repo_id: UUID,
    ) -> None:
        project = await self.get(user_id=user_id, project_id=project_id)
        stmt = select(ProjectRepo).where(
            ProjectRepo.id == repo_id,
            ProjectRepo.project_id == project.id,
        )
        repo = (await self._session.execute(stmt)).scalar_one_or_none()
        if repo is None:
            raise NotFoundError(
                f"Repository {repo_id} was not found in this project.",
            )
        await self._session.delete(repo)
