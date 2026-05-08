"""Database access for TaskApproval."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.task_approval import ApprovalStatus, TaskApproval
from src.utils.exceptions import NotFoundError


class TaskApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        task_id: UUID,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> TaskApproval:
        approval = TaskApproval(
            user_id=user_id,
            task_id=task_id,
            tool_name=tool_name,
            tool_input=tool_input,
            status=ApprovalStatus.PENDING,
        )
        self._session.add(approval)
        await self._session.flush()
        return approval

    async def get(
        self, *, user_id: int, task_id: UUID, approval_id: UUID
    ) -> TaskApproval:
        stmt = select(TaskApproval).where(
            TaskApproval.id == approval_id,
            TaskApproval.task_id == task_id,
            TaskApproval.user_id == user_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundError(f"Approval {approval_id} was not found.")
        return row

    async def list_for_task(
        self, *, user_id: int, task_id: UUID
    ) -> list[TaskApproval]:
        stmt = (
            select(TaskApproval)
            .where(TaskApproval.task_id == task_id, TaskApproval.user_id == user_id)
            .order_by(TaskApproval.created_at.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def resolve(
        self, *, approval_id: UUID, status: ApprovalStatus
    ) -> TaskApproval:
        stmt = select(TaskApproval).where(TaskApproval.id == approval_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundError(f"Approval {approval_id} was not found.")
        row.status = status
        await self._session.flush()
        return row
