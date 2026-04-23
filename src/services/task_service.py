"""Task service: persistence and pipeline orchestration.

When a task is created the service persists it and fires a background coroutine
that streams the entire development pipeline. The pipeline runs inside the same
uvicorn event loop as the HTTP handler so we do not need Celery or any external
queue — the 95% of time is spent waiting on LLM and GitHub API calls, which are
already async and release the thread naturally.

The background runner owns its own DB session via `db.session_scope()` so it is
not affected by the request session closing.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog

from src.api.schemas.task_schemas import TaskCreate
from src.db.models.task import Task, TaskStatus
from src.db.queries.project_queries import ProjectRepository
from src.db.queries.task_queries import TaskRepository
from src.config.constants import (
    WS_EVENT_PIPELINE_FAILED,
    WS_EVENT_TASK_STATUS_CHANGED,
)
from src.db.session import db
from src.engine.broadcaster import broadcaster


class TaskService:
    def __init__(
        self,
        *,
        repository: TaskRepository,
        project_repository: ProjectRepository,
    ) -> None:
        self._repo = repository
        self._project_repo = project_repository
        self._logger = structlog.get_logger("clyde.service.task")

    async def create(self, *, user_id: int, payload: TaskCreate) -> Task:
        project = await self._project_repo.get(
            user_id=user_id, project_id=payload.project_id
        )
        task = await self._repo.create(
            user_id=user_id,
            project_id=payload.project_id,
            description=payload.description,
        )

        initial_state = {
            "task_id": str(task.id),
            "user_id": user_id,
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

        asyncio.create_task(
            self._run_pipeline_background(
                task_id=task.id,
                user_id=user_id,
                initial_state=initial_state,
            )
        )
        self._logger.info("task.created", task_id=str(task.id), project_id=str(task.project_id))
        return task

    async def get(self, *, user_id: int, task_id: UUID) -> Task:
        return await self._repo.get(user_id=user_id, task_id=task_id)

    async def retry(self, *, user_id: int, task_id: UUID) -> Task:
        """Resume a failed task from its last checkpoint.

        LangGraph saves state after every successful agent step. When the
        pipeline failed at, say, Developer agent — this method re-enters
        the graph at that exact point without re-running earlier agents.
        Only the failed step and everything after it
        is re-executed.

        Falls back to a full restart if no checkpoint exists.
        """
        from src.common.exceptions import ConflictError

        task = await self._repo.get(user_id=user_id, task_id=task_id)
        if task.status not in (TaskStatus.FAILED, TaskStatus.NEEDS_HUMAN):
            raise ConflictError(
                f"Task {task_id} is in status {task.status} and cannot be retried."
            )

        await self._repo.update_status(
            task=task,
            status=TaskStatus.RUNNING,
            error_message=None,
        )

        # Re-fetch to ensure all attributes are loaded (updated_at changed by DB trigger)
        task = await self._repo.get(user_id=user_id, task_id=task_id)

        asyncio.create_task(
            self._resume_pipeline_background(
                task_id=task.id,
                user_id=user_id,
            )
        )
        self._logger.info("task.resumed_from_checkpoint", task_id=str(task.id))
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
        return await self._repo.list(
            user_id=user_id,
            offset=offset,
            limit=limit,
            project_id=project_id,
            status=status,
        )

    async def transition(
        self,
        *,
        task: Task,
        status: TaskStatus,
        attempt: int | None = None,
        error_message: str | None = None,
        state_patch: dict[str, Any] | None = None,
        pr_urls_patch: dict[str, str] | None = None,
    ) -> Task:
        """Internal helper used by the engine and webhook handlers."""
        return await self._repo.update_status(
            task=task,
            status=status,
            attempt=attempt,
            error_message=error_message,
            state_patch=state_patch,
            pr_urls_patch=pr_urls_patch,
        )

    async def _run_pipeline_background(
        self,
        *,
        task_id: UUID,
        user_id: int,
        initial_state: dict[str, Any],
    ) -> None:
        """Background coroutine that drives the pipeline from start to end.

        Runs with its own session so the request session closing does not
        affect it. Catches all exceptions and transitions the task to an
        appropriate terminal status.
        """
        from src.engine import runtime

        log = self._logger.bind(task_id=str(task_id))
        log.info("pipeline.background.started")

        try:
            executor = runtime.executor
            final_state: dict[str, Any] = {}
            async for event in executor.stream(task_id=task_id, initial_state=initial_state):
                final_state = event
                await broadcaster.publish(task_id, event)

            persisted_state = await executor.get_state(task_id=task_id)
            pr_urls = persisted_state.get("pr_urls")

            if pr_urls:
                new_status = TaskStatus.AWAITING_CI
            else:
                new_status = TaskStatus.COMPLETED

            async with db.session_scope() as session:
                repo = TaskRepository(session)
                task = await repo.get(user_id=user_id, task_id=task_id)
                await repo.update_status(
                    task=task,
                    status=new_status,
                    state_patch=persisted_state,
                    pr_urls_patch=pr_urls or None,
                )

            # Notify subscribers that the pipeline reached a terminal status.
            await broadcaster.publish(task_id, {
                "name": WS_EVENT_TASK_STATUS_CHANGED,
                "agent": None,
                "payload": {"status": new_status.value},
                "occurred_at": "",
            })
            await broadcaster.close_task(task_id)

            log.info("pipeline.background.finished", status=new_status.value)

        except Exception as exc:
            log.exception("pipeline.background.failed", error=str(exc))

            # Let subscribers know the pipeline failed before closing the stream.
            await broadcaster.publish(task_id, {
                "name": WS_EVENT_PIPELINE_FAILED,
                "agent": None,
                "payload": {"error": str(exc)[:2000]},
                "occurred_at": "",
            })
            await broadcaster.close_task(task_id)

            try:
                async with db.session_scope() as session:
                    repo = TaskRepository(session)
                    task = await repo.get(user_id=user_id, task_id=task_id)
                    await repo.update_status(
                        task=task,
                        status=TaskStatus.FAILED,
                        error_message=str(exc)[:2000],
                    )
            except Exception:
                log.exception("pipeline.background.status_update_failed")

    async def _resume_pipeline_background(
        self,
        *,
        task_id: UUID,
        user_id: int,
        patch: dict[str, Any] | None = None,
    ) -> None:
        """Resume a pipeline from its last LangGraph checkpoint.

        LangGraph stores a checkpoint after every successful node. Calling
        `executor.resume(task_id)` re-enters the graph at the node that
        failed (or the next unexecuted node), skipping all the agents that
        already ran successfully. This typically saves minutes of LLM calls.

        `patch` allows injecting state changes before resuming.
        """
        from src.engine import runtime

        log = self._logger.bind(task_id=str(task_id))
        log.info("pipeline.resume.started")

        try:
            executor = runtime.executor

            # Check if a checkpoint exists for this task.
            existing_state = await executor.get_state(task_id=task_id)
            if not existing_state:
                log.warning("pipeline.resume.no_checkpoint_found")
                # No checkpoint — need a full restart. Fetch project repos.
                async with db.session_scope() as session:
                    from src.db.queries.project_queries import ProjectRepository
                    task_repo = TaskRepository(session)
                    task = await task_repo.get(user_id=user_id, task_id=task_id)
                    project_repo = ProjectRepository(session)
                    project = await project_repo.get(
                        user_id=user_id, project_id=task.project_id
                    )
                    initial_state = {
                        "task_id": str(task.id),
                        "user_id": user_id,
                        "project_id": str(task.project_id),
                        "description": task.description,
                        "repos": [
                            {
                                "name": r.url.rsplit("/", 1)[-1].removesuffix(".git"),
                                "url": r.url,
                                "default_branch": r.default_branch,
                            }
                            for r in project.repos
                        ],
                        "attempt": 0,
                        "events": [],
                    }
                await self._run_pipeline_background(
                    task_id=task_id, user_id=user_id, initial_state=initial_state
                )
                return

            log.info("pipeline.resume.checkpoint_found", state_keys=list(existing_state.keys()))
            final_state: dict[str, Any] = {}
            async for event in executor.resume(task_id=task_id, patch=patch):
                final_state = event
                await broadcaster.publish(task_id, event)

            persisted_state = await executor.get_state(task_id=task_id)
            pr_urls = persisted_state.get("pr_urls")

            new_status = TaskStatus.AWAITING_CI if pr_urls else TaskStatus.COMPLETED

            async with db.session_scope() as session:
                repo = TaskRepository(session)
                task = await repo.get(user_id=user_id, task_id=task_id)
                await repo.update_status(
                    task=task,
                    status=new_status,
                    state_patch=persisted_state,
                    pr_urls_patch=pr_urls or None,
                )

            await broadcaster.publish(task_id, {
                "name": WS_EVENT_TASK_STATUS_CHANGED,
                "agent": None,
                "payload": {"status": new_status.value},
                "occurred_at": "",
            })
            await broadcaster.close_task(task_id)
            log.info("pipeline.resume.finished", status=new_status.value)

        except Exception as exc:
            log.exception("pipeline.resume.failed", error=str(exc))
            await broadcaster.publish(task_id, {
                "name": WS_EVENT_PIPELINE_FAILED,
                "agent": None,
                "payload": {"error": str(exc)[:2000]},
                "occurred_at": "",
            })
            await broadcaster.close_task(task_id)
            try:
                async with db.session_scope() as session:
                    repo = TaskRepository(session)
                    task = await repo.get(user_id=user_id, task_id=task_id)
                    await repo.update_status(
                        task=task,
                        status=TaskStatus.FAILED,
                        error_message=str(exc)[:2000],
                    )
            except Exception:
                log.exception("pipeline.resume.status_update_failed")
