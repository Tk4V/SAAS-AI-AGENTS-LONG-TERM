"""HTTP views for task approval listing.

Resolution of pending approvals is handled exclusively over the bidirectional
WebSocket ``/ws/tasks/{id}/chat`` (see ``task_stream_view``). This module
keeps a read-only listing endpoint so the UI can bootstrap the set of
currently pending approvals on first load or after a reconnect.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from src.api.dependencies import CurrentUserDep, TaskApprovalRepositoryDep, TaskRepositoryDep
from src.api.schemas.task_approval_schemas import TaskApprovalRead

router = APIRouter(prefix="/tasks", tags=["Tasks"])


class TaskApprovalsView:
    """List pending and resolved tool-use approvals for a task."""

    @staticmethod
    @router.get("/{task_id}/approvals", response_model=list[TaskApprovalRead])
    async def list_approvals(
        task_id: UUID,
        user: CurrentUserDep,
        approval_repo: TaskApprovalRepositoryDep,
        task_repo: TaskRepositoryDep,
    ) -> list[TaskApprovalRead]:
        """Return all approvals for a task (pending and resolved)."""
        # Ownership check.
        await task_repo.get(user_id=user.id, task_id=task_id)
        approvals = await approval_repo.list_for_task(user_id=user.id, task_id=task_id)
        return [TaskApprovalRead.from_orm(a) for a in approvals]
