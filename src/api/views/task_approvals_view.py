"""HTTP views for task approval management."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from src.agent_tools import permission_gate
from src.api.dependencies import CurrentUserDep, TaskApprovalRepositoryDep, TaskRepositoryDep
from src.api.schemas.task_approval_schemas import TaskApprovalRead, TaskApprovalResolve
from src.db.models.task_approval import ApprovalStatus
from src.utils.exceptions import ConflictError

router = APIRouter(prefix="/tasks", tags=["Tasks"])


class TaskApprovalsView:
    """Endpoints to list and resolve pending tool-use approvals for a task."""

    @staticmethod
    @router.get("/{task_id}/approvals", response_model=list[TaskApprovalRead])
    async def list_approvals(
        task_id: UUID,
        user: CurrentUserDep,
        approval_repo: TaskApprovalRepositoryDep,
        task_repo: TaskRepositoryDep,
    ) -> list[TaskApprovalRead]:
        """Return all approvals for a task (pending and resolved)."""
        # Validates task ownership.
        await task_repo.get(user_id=user.id, task_id=task_id)
        approvals = await approval_repo.list_for_task(user_id=user.id, task_id=task_id)
        return [TaskApprovalRead.from_orm(a) for a in approvals]

    @staticmethod
    @router.post(
        "/{task_id}/approvals/{approval_id}/resolve",
        response_model=TaskApprovalRead,
        status_code=status.HTTP_200_OK,
    )
    async def resolve_approval(
        task_id: UUID,
        approval_id: UUID,
        payload: TaskApprovalResolve,
        user: CurrentUserDep,
        approval_repo: TaskApprovalRepositoryDep,
        task_repo: TaskRepositoryDep,
    ) -> TaskApprovalRead:
        """Approve or deny a pending tool-use request.

        Wakes the paused pipeline coroutine so the agent can continue or
        receive a denial message.
        """
        approval = await approval_repo.get(
            user_id=user.id, task_id=task_id, approval_id=approval_id
        )
        if approval.status != ApprovalStatus.PENDING:
            raise ConflictError(
                f"Approval {approval_id} is already {approval.status.value}."
            )

        new_status = ApprovalStatus.APPROVED if payload.approved else ApprovalStatus.DENIED
        approval = await approval_repo.resolve(approval_id=approval_id, status=new_status)

        # Wake the blocked can_use_tool callback in the pipeline coroutine.
        permission_gate.resolve(approval_id=approval_id, approved=payload.approved)

        return TaskApprovalRead.from_orm(approval)
