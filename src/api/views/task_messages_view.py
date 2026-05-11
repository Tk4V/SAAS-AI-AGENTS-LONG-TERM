"""HTTP view for the task chat history bootstrap."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query

from src.api.dependencies import (
    CurrentUserDep,
    TaskMessageRepositoryDep,
    TaskRepositoryDep,
)
from src.api.schemas.task_message_schemas import TaskMessageRead

router = APIRouter(prefix="/tasks", tags=["Tasks"])


class TaskMessagesView:
    """List the chat history for a task. Used by the UI to bootstrap the
    chat panel before subscribing to live deltas via WebSocket."""

    @staticmethod
    @router.get("/{task_id}/messages", response_model=list[TaskMessageRead])
    async def list_messages(
        task_id: UUID,
        user: CurrentUserDep,
        message_repo: TaskMessageRepositoryDep,
        task_repo: TaskRepositoryDep,
        limit: int = Query(50, ge=1, le=200),
        before: datetime | None = Query(
            None,
            description="ISO timestamp; only messages strictly older are returned. "
                        "Use the oldest message's created_at to page backwards.",
        ),
    ) -> list[TaskMessageRead]:
        # Ownership check.
        await task_repo.get(user_id=user.id, task_id=task_id)
        messages = await message_repo.list_for_task(
            user_id=user.id, task_id=task_id, limit=limit, before=before
        )
        return [TaskMessageRead.from_orm(m) for m in messages]
