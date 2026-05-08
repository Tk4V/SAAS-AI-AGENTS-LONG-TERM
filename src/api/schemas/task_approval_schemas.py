"""Pydantic schemas for the task approvals resource."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.db.models.task_approval import ApprovalStatus

if TYPE_CHECKING:
    from src.db.models.task_approval import TaskApproval


class TaskApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    tool_name: str
    tool_input: dict[str, Any]
    status: ApprovalStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, approval: "TaskApproval") -> "TaskApprovalRead":
        return cls(
            id=approval.id,
            task_id=approval.task_id,
            tool_name=approval.tool_name,
            tool_input=approval.tool_input or {},
            status=approval.status,
            created_at=approval.created_at,
            updated_at=approval.updated_at,
        )


class TaskApprovalResolve(BaseModel):
    approved: bool
