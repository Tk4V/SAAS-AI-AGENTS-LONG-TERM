"""Pydantic schemas for the tasks resource."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.db.models.task import TaskStatus

if TYPE_CHECKING:
    from src.db.models.task import Task


class TaskCreate(BaseModel):
    project_id: UUID
    description: str = Field(min_length=1, max_length=10_000)
    # Optional — falls back to the user's default agent server-side.
    agent_id: UUID | None = None


class TaskBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    agent_id: UUID
    description: str
    status: TaskStatus
    attempt: int
    pr_urls: dict[str, str]
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class TaskListItem(TaskBase):
    """Slim view for list endpoints."""

    @classmethod
    def from_orm(cls, task: "Task") -> "TaskListItem":
        return cls(
            id=task.id,
            project_id=task.project_id,
            agent_id=task.agent_id,
            description=task.description,
            status=task.status,
            attempt=task.attempt,
            pr_urls=task.pr_urls or {},
            error_message=task.error_message,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


class TaskRead(TaskBase):
    """Detailed view including the internal pipeline state snapshot."""

    state: dict[str, Any]

    @classmethod
    def from_orm(cls, task: "Task") -> "TaskRead":
        return cls(
            id=task.id,
            project_id=task.project_id,
            agent_id=task.agent_id,
            description=task.description,
            status=task.status,
            attempt=task.attempt,
            pr_urls=task.pr_urls or {},
            error_message=task.error_message,
            state=task.state or {},
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
