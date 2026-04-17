"""Webhook service: signature verification and CI event routing.

GitHub sends a `workflow_run` webhook when a CI run completes. This service
verifies the HMAC-SHA256 signature, locates the Clyde task that owns the
branch, and either marks it complete or spawns the DevOps Engineer agent to
attempt a fix.

The fix loop is intentionally simple: we run the DevOps agent directly
(outside the LangGraph pipeline) and then transition the task back to
AWAITING_CI. The next webhook will either succeed or trigger another fix
attempt until MAX_FIX_ATTEMPTS is exhausted.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.development_team.devops_engineer.agent import DevOpsEngineerAgent
from src.api.schemas.webhook_schemas import GitHubWorkflowRunPayload
from src.config import Settings, get_settings
from src.config.constants import (
    WS_EVENT_TASK_STATUS_CHANGED,
)
from src.db.models.task import Task, TaskStatus
from src.db.queries.task_queries import TaskRepository
from src.db.session import Database, db
from src.engine.broadcaster import broadcaster


# Branch names created by Release Manager follow this pattern:
#   clyde/{task_id[:8]}/{repo_name}
_BRANCH_PATTERN = re.compile(r"^clyde/(?P<prefix>[0-9a-f]{8})/(?P<repo>.+)$")


class WebhookService:
    """Verifies GitHub webhook signatures and routes CI events to tasks."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        database: Database | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._database = database or db
        self._logger = structlog.get_logger("clyde.service.webhook")

    def verify_signature(self, *, payload: bytes, signature: str) -> bool:
        """HMAC-SHA256 verification against GITHUB_WEBHOOK_SECRET.

        GitHub sends the header as ``sha256=<hex>``. We strip the prefix and
        compare with a constant-time comparison to avoid timing attacks.
        """
        secret = self._settings.github_webhook_secret.get_secret_value()
        if not secret:
            self._logger.error("webhook.secret_not_configured")
            return False

        expected = hmac.new(
            secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()

        # The header value looks like "sha256=abc123..."
        received = signature.removeprefix("sha256=")
        return hmac.compare_digest(expected, received)

    async def find_task_for_branch(
        self,
        *,
        session: AsyncSession,
        repo_full_name: str,
        branch: str,
    ) -> Task | None:
        """Look up the task that created this branch.

        Branch format: clyde/{task_id[:8]}/{repo_name}
        We parse the 8-char UUID prefix and search the tasks table with a
        LIKE on the cast-to-text ID column.
        """
        match = _BRANCH_PATTERN.match(branch)
        if not match:
            return None

        prefix = match.group("prefix")

        # task.id is a UUID; cast to text so we can match the prefix.
        stmt = (
            select(Task)
            .where(cast(Task.id, String).like(f"{prefix}%"))
            .order_by(Task.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def handle_workflow_run(
        self,
        *,
        session: AsyncSession,
        payload: GitHubWorkflowRunPayload,
    ) -> None:
        """Main handler: find the task, check the conclusion, act accordingly."""
        run_data = payload.workflow_run
        log = self._logger.bind(
            run_id=run_data.id,
            branch=run_data.head_branch,
            conclusion=run_data.conclusion,
            repo=run_data.repository.full_name,
        )

        if payload.action != "completed":
            log.debug("webhook.ignored_action", action=payload.action)
            return

        task = await self.find_task_for_branch(
            session=session,
            repo_full_name=run_data.repository.full_name,
            branch=run_data.head_branch,
        )
        if task is None:
            log.info("webhook.no_matching_task")
            return

        log = log.bind(task_id=str(task.id), task_status=task.status.value)

        # Guard against duplicate webhooks or webhooks arriving after the task
        # has already reached a terminal state.
        terminal = {TaskStatus.COMPLETED, TaskStatus.NEEDS_HUMAN, TaskStatus.FAILED}
        if task.status in terminal:
            log.info("webhook.task_already_terminal")
            return

        if task.status == TaskStatus.FIXING:
            log.info("webhook.task_already_fixing")
            return

        repo = TaskRepository(session)

        if run_data.conclusion == "success":
            await repo.update_status(task=task, status=TaskStatus.COMPLETED)
            log.info("webhook.task_completed")

            await broadcaster.publish(task.id, {
                "name": WS_EVENT_TASK_STATUS_CHANGED,
                "agent": None,
                "payload": {"status": TaskStatus.COMPLETED.value},
                "occurred_at": "",
            })
            await broadcaster.close_task(task.id)
            return

        if run_data.conclusion == "failure":
            max_attempts = self._settings.max_fix_attempts
            if task.attempt >= max_attempts:
                await repo.update_status(task=task, status=TaskStatus.NEEDS_HUMAN)
                log.info("webhook.max_attempts_exhausted", attempts=task.attempt)

                await broadcaster.publish(task.id, {
                    "name": WS_EVENT_TASK_STATUS_CHANGED,
                    "agent": None,
                    "payload": {"status": TaskStatus.NEEDS_HUMAN.value},
                    "occurred_at": "",
                })
                await broadcaster.close_task(task.id)
                return

            # Transition to FIXING and spawn the DevOps Engineer in the background.
            await repo.update_status(task=task, status=TaskStatus.FIXING)
            log.info("webhook.spawning_fix", attempt=task.attempt + 1)

            await broadcaster.publish(task.id, {
                "name": WS_EVENT_TASK_STATUS_CHANGED,
                "agent": None,
                "payload": {"status": TaskStatus.FIXING.value},
                "occurred_at": "",
            })

            asyncio.create_task(
                self._run_devops_fix(
                    task_id=task.id,
                    user_id=task.user_id,
                    task_state=task.state or {},
                    attempt=task.attempt,
                    ci_run_id=run_data.id,
                    ci_repo_full_name=run_data.repository.full_name,
                )
            )
            return

        # For any other conclusion (cancelled, timed_out, etc.) we just log it.
        log.info("webhook.unhandled_conclusion", conclusion=run_data.conclusion)

    async def _run_devops_fix(
        self,
        *,
        task_id: UUID,
        user_id: int,
        task_state: dict[str, Any],
        attempt: int,
        ci_run_id: int,
        ci_repo_full_name: str,
    ) -> None:
        """Background coroutine that runs the DevOps Engineer agent directly.

        Owns its own DB session so the request session closing doesn't affect it.
        On success the task transitions to AWAITING_CI (the push triggers CI
        again). On failure it transitions to FAILED.
        """
        log = self._logger.bind(task_id=str(task_id), attempt=attempt + 1)
        log.info("devops_fix.started")

        try:
            agent = DevOpsEngineerAgent()

            # Build a minimal state dict with everything the agent needs.
            state: dict[str, Any] = {
                **task_state,
                "task_id": str(task_id),
                "user_id": user_id,
                "attempt": attempt,
                "ci_run_id": ci_run_id,
                "ci_repo_full_name": ci_repo_full_name,
                "max_fix_attempts": self._settings.max_fix_attempts,
            }

            result = await agent(state)

            # Persist the updated state and transition back to AWAITING_CI.
            async with self._database.session_scope() as session:
                repo = TaskRepository(session)
                task = await repo.get(user_id=user_id, task_id=task_id)
                await repo.update_status(
                    task=task,
                    status=TaskStatus.AWAITING_CI,
                    attempt=result.get("attempt", attempt + 1),
                    state_patch={
                        "diffs": result.get("diffs", {}),
                        "attempt": result.get("attempt", attempt + 1),
                    },
                )

            await broadcaster.publish(task_id, {
                "name": WS_EVENT_TASK_STATUS_CHANGED,
                "agent": None,
                "payload": {"status": TaskStatus.AWAITING_CI.value},
                "occurred_at": "",
            })

            log.info("devops_fix.completed")

        except Exception as exc:
            log.exception("devops_fix.failed", error=str(exc))
            try:
                async with self._database.session_scope() as session:
                    repo = TaskRepository(session)
                    task = await repo.get(user_id=user_id, task_id=task_id)
                    await repo.update_status(
                        task=task,
                        status=TaskStatus.FAILED,
                        error_message=f"DevOps fix failed: {str(exc)[:500]}",
                    )
            except Exception:
                log.exception("devops_fix.status_update_failed")

            await broadcaster.publish(task_id, {
                "name": WS_EVENT_TASK_STATUS_CHANGED,
                "agent": None,
                "payload": {"status": TaskStatus.FAILED.value},
                "occurred_at": "",
            })
            await broadcaster.close_task(task_id)


