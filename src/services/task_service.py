"""Task service: persistence and chat-session orchestration."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog

from src.api.schemas.task_schemas import TaskCreate
from src.db.models.task import Task, TaskStatus
from src.db.queries.project_query import ProjectRepository
from src.db.queries.task_query import TaskRepository
from src.config.constants import WS_EVENT_PIPELINE_FAILED
from src.db.session import db
from src.utils.broadcaster import broadcaster

# Agents and chat session imports are intentionally deferred to
# ``_run_pipeline``. Eager-loading them here pulls the OrchestratorAgent
# → SDKAgent → BaseAgent chain through the ``services/__init__`` re-
# exports, which loops back into BaseAgent and breaks at test import.


class TaskService:
    """Manages task lifecycle and pipeline execution."""

    def __init__(self, *, repository: TaskRepository, project_repository: ProjectRepository) -> None:
        self._repo = repository
        self._project_repo = project_repository
        self._logger = structlog.get_logger("clyde.service.task")

    async def create(self, *, user_id: int, payload: TaskCreate, agent_id: UUID) -> Task:
        """Create a task and spawn the pipeline in the background.

        ``agent_id`` is resolved by the view from ``AgentService`` (either
        the body's explicit value or the user's default agent). It must be
        a valid agent owned by the user — the FK on ``tasks`` plus the
        view-level ownership check are the safety net.
        """
        project = await self._project_repo.get(user_id=user_id, project_id=payload.project_id)
        task = await self._repo.create(
            user_id=user_id,
            project_id=payload.project_id,
            agent_id=agent_id,
            description=payload.description,
        )
        initial_state = self._build_initial_state(task=task, user_id=user_id, project=project)
        asyncio.create_task(self._run_pipeline(task_id=task.id, user_id=user_id, initial_state=initial_state))
        self._logger.info("task.created", task_id=str(task.id), agent_id=str(agent_id))
        return task

    async def get(self, *, user_id: int, task_id: UUID) -> Task:
        """Fetch a single task by ID."""
        return await self._repo.get(user_id=user_id, task_id=task_id)

    async def list(self, *, user_id: int, offset: int, limit: int, project_id: UUID | None = None, status: TaskStatus | None = None) -> tuple[list[Task], int]:
        """List tasks with optional filters."""
        return await self._repo.list(user_id=user_id, offset=offset, limit=limit, project_id=project_id, status=status)

    async def delete(self, *, user_id: int, task_id: UUID) -> None:
        """Delete a task by ID."""
        await self._repo.delete(user_id=user_id, task_id=task_id)
        self._logger.info("task.deleted", task_id=str(task_id))

    async def retry(self, *, user_id: int, task_id: UUID) -> Task:
        """Restart a failed task from scratch."""
        from src.utils.exceptions import ConflictError

        task = await self._repo.get(user_id=user_id, task_id=task_id)
        if task.status not in (TaskStatus.FAILED, TaskStatus.NEEDS_HUMAN):
            raise ConflictError(f"Task {task_id} is in status {task.status} and cannot be retried.")

        await self._repo.update_status(task=task, status=TaskStatus.RUNNING, error_message=None)
        task = await self._repo.get(user_id=user_id, task_id=task_id)

        project = await self._project_repo.get(user_id=user_id, project_id=task.project_id)
        initial_state = self._build_initial_state(task=task, user_id=user_id, project=project)
        asyncio.create_task(self._run_pipeline(task_id=task.id, user_id=user_id, initial_state=initial_state))
        self._logger.info("task.retried", task_id=str(task.id))
        return task

    async def transition(self, *, task: Task, status: TaskStatus, attempt: int | None = None, error_message: str | None = None, state_patch: dict[str, Any] | None = None, pr_urls_patch: dict[str, str] | None = None) -> Task:
        """Update task status. Used by webhook handlers."""
        return await self._repo.update_status(task=task, status=status, attempt=attempt, error_message=error_message, state_patch=state_patch, pr_urls_patch=pr_urls_patch)

    @staticmethod
    def _build_initial_state(*, task: Task, user_id: int, project: Any) -> dict[str, Any]:
        """Construct the initial pipeline state from a task and its project."""
        return {
            "task_id": str(task.id),
            "user_id": user_id,
            "agent_id": str(task.agent_id),
            "project_id": str(task.project_id),
            "description": task.description,
            "repos": [
                {
                    "name": repo.url.rsplit("/", 1)[-1].removesuffix(".git"),
                    "url": repo.url,
                    "default_branch": repo.default_branch,
                }
                for repo in project.repos
            ],
            "attempt": 0,
            "events": [],
        }

    async def _run_pipeline(self, *, task_id: UUID, user_id: int, initial_state: dict[str, Any]) -> None:
        """Background coroutine that bootstraps the orchestrator chat session.

        Lifecycle since CA-113:
          1. Orchestrator builds a persistent ``SDKChatSession`` bound to this
             task's repo workspace + tools + hooks.
          2. The session runs the initial user-turn (task description), then
             stays open feeding subsequent ``chat_message`` envelopes from
             the WS handler into the same SDK conversation.
          3. The post-turn callback (auto-publisher, wired in Phase 2)
             commits and pushes any diffs after each turn.
          4. The session ends on user close, idle/hard timeout, or error —
             status transitions are owned by ``SDKChatSession`` itself, so
             this coroutine only handles the catastrophic-failure path
             (orchestrator crashed before the session even started).
        """
        log = self._logger.bind(task_id=str(task_id))
        log.info("pipeline.started")

        try:
            from src.agents.chat.turn_handler import build_post_turn_callback
            from src.agents.team.orchestrator_agent import OrchestratorAgent

            state = {
                **initial_state,
                "_post_turn_callback": build_post_turn_callback(
                    task_id=task_id, user_id=user_id,
                ),
            }
            orchestrator = OrchestratorAgent()
            await orchestrator(state)
            log.info("pipeline.finished")

        except Exception as exc:
            log.exception("pipeline.failed", error=str(exc))
            await broadcaster.publish(task_id, {
                "name": WS_EVENT_PIPELINE_FAILED, "agent": None,
                "payload": {"error": str(exc)[:2000]}, "occurred_at": "",
            })
            await broadcaster.close_task(task_id)
            try:
                async with db.session_scope() as session:
                    repo = TaskRepository(session)
                    task = await repo.get(user_id=user_id, task_id=task_id)
                    await repo.update_status(task=task, status=TaskStatus.FAILED, error_message=str(exc)[:2000])
            except Exception:
                log.exception("pipeline.status_update_failed")
