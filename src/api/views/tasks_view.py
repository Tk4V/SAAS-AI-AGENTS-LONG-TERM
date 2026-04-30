"""HTTP views for task management."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from src.api.dependencies import CurrentUserDep, TaskServiceDep
from src.api.schemas.common_schemas import Page, PaginationParams
from src.api.schemas.task_schemas import TaskCreate, TaskListItem, TaskRead
from src.db.models.task import TaskStatus

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskView:
    """CRUD operations and pipeline control for tasks."""

    @staticmethod
    @router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
    async def create(
        payload: TaskCreate,
        user: CurrentUserDep,
        service: TaskServiceDep,
    ) -> TaskRead:
        """Create a new task and start the pipeline in the background."""
        task = await service.create(user_id=user.id, payload=payload)
        return TaskRead.from_orm(task)

    @staticmethod
    @router.get("", response_model=Page[TaskListItem])
    async def list(
        user: CurrentUserDep,
        service: TaskServiceDep,
        pagination: Annotated[PaginationParams, Depends()],
        project_id: Annotated[
            UUID | None, Query(description="Filter by project.")
        ] = None,
        task_status: Annotated[
            TaskStatus | None,
            Query(alias="status", description="Filter by task status."),
        ] = None,
    ) -> Page[TaskListItem]:
        """List tasks with optional project and status filters."""
        items, total = await service.list(
            user_id=user.id,
            offset=pagination.offset,
            limit=pagination.limit,
            project_id=project_id,
            status=task_status,
        )
        return Page[TaskListItem](
            items=[TaskListItem.from_orm(task) for task in items],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    @staticmethod
    @router.get("/{task_id}", response_model=TaskRead)
    async def get(
        task_id: UUID,
        user: CurrentUserDep,
        service: TaskServiceDep,
    ) -> TaskRead:
        """Fetch a single task by ID."""
        task = await service.get(user_id=user.id, task_id=task_id)
        return TaskRead.from_orm(task)

    @staticmethod
    @router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete(
        task_id: UUID,
        user: CurrentUserDep,
        service: TaskServiceDep,
    ) -> None:
        """Delete a task by ID."""
        await service.delete(user_id=user.id, task_id=task_id)

    @staticmethod
    @router.post("/{task_id}/retry", response_model=TaskRead)
    async def retry(
        task_id: UUID,
        user: CurrentUserDep,
        service: TaskServiceDep,
    ) -> TaskRead:
        """Restart a failed task from the beginning."""
        task = await service.retry(user_id=user.id, task_id=task_id)
        return TaskRead.from_orm(task)
