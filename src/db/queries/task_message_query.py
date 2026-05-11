"""Database access for TaskMessage."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.task_message import MessageKind, MessageRole, TaskMessage


class TaskMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        task_id: UUID,
        role: MessageRole,
        kind: MessageKind = MessageKind.CHAT,
        content: str = "",
        author: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> TaskMessage:
        message = TaskMessage(
            user_id=user_id,
            task_id=task_id,
            role=role,
            kind=kind,
            content=content,
            author=author,
            meta=meta or {},
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def list_for_task(
        self,
        *,
        user_id: int,
        task_id: UUID,
        limit: int = 50,
        before: datetime | None = None,
    ) -> list[TaskMessage]:
        """Return messages oldest-first, optionally filtered to those created
        before a cursor timestamp for pagination ("load older messages")."""
        stmt = select(TaskMessage).where(
            TaskMessage.task_id == task_id,
            TaskMessage.user_id == user_id,
        )
        if before is not None:
            stmt = stmt.where(TaskMessage.created_at < before)
        stmt = stmt.order_by(TaskMessage.created_at.desc()).limit(limit)
        rows = list((await self._session.execute(stmt)).scalars().all())
        rows.reverse()
        return rows
