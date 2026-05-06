"""Database access for Task.

`update_status` is intentionally part of the repository surface but is meant
to be called only from `TaskService` — every status transition has business
meaning that belongs in the service.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.utils.exceptions import NotFoundError
from src.db.models.project import Project
from src.db.models.task import Task, TaskStatus


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        project_id: UUID,
        agent_id: UUID,
        description: str,
        status: TaskStatus = TaskStatus.RUNNING,
    ) -> Task:
        owns_project = await self._session.scalar(
            select(func.count(Project.id)).where(
                Project.id == project_id,
                Project.user_id == user_id,
            )
        )
        if not owns_project:
            raise NotFoundError(f"Project {project_id} was not found.")

        task = Task(
            user_id=user_id,
            project_id=project_id,
            agent_id=agent_id,
            description=description,
            status=status,
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def get(self, *, user_id: int, task_id: UUID) -> Task:
        stmt = select(Task).where(Task.id == task_id, Task.user_id == user_id)
        task = (await self._session.execute(stmt)).scalar_one_or_none()
        if task is None:
            raise NotFoundError(f"Task {task_id} was not found.")
        return task

    async def list(
        self,
        *,
        user_id: int,
        offset: int,
        limit: int,
        project_id: UUID | None = None,
        status: TaskStatus | None = None,
    ) -> tuple[list[Task], int]:
        base = select(Task).where(Task.user_id == user_id)
        count_base = select(func.count(Task.id)).where(Task.user_id == user_id)

        if project_id is not None:
            base = base.where(Task.project_id == project_id)
            count_base = count_base.where(Task.project_id == project_id)
        if status is not None:
            base = base.where(Task.status == status)
            count_base = count_base.where(Task.status == status)

        stmt = base.order_by(Task.created_at.desc()).offset(offset).limit(limit)
        items = list((await self._session.execute(stmt)).scalars().all())
        total = await self._session.scalar(count_base)
        return items, int(total or 0)

    async def delete(self, *, user_id: int, task_id: UUID) -> None:
        task = await self.get(user_id=user_id, task_id=task_id)
        await self._session.delete(task)
        await self._session.flush()

    async def update_status(
        self,
        *,
        task: Task,
        status: TaskStatus,
        attempt: int | None = None,
        error_message: str | None = None,
        state_patch: dict[str, Any] | None = None,
        pr_urls_patch: dict[str, str] | None = None,
    ) -> Task:
        task.status = status
        if attempt is not None:
            task.attempt = attempt
        if error_message is not None:
            task.error_message = error_message
        if state_patch:
            task.state = {**(task.state or {}), **state_patch}
        if pr_urls_patch:
            task.pr_urls = {**(task.pr_urls or {}), **pr_urls_patch}
        await self._session.flush()
        return task
