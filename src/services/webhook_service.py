"""Webhook service: signature verification and CI event routing.

GitHub sends a ``workflow_run`` webhook when a CI run completes. This
service verifies the HMAC-SHA256 signature, locates the Clyde task that
owns the branch, and either marks it complete or kicks off a CI-failure
fix loop.

On CI failure (``conclusion == "failure"``) the service spawns a
background task that re-runs the agent pipeline (``OrchestratorAgent``
→ ``PublisherAgent``) with a CI-failure-marked prompt, so the
orchestrator delegates diagnosis and fix to the ``devops`` sub-agent.
After ``max_fix_attempts`` without success the task transitions to
``NEEDS_HUMAN``.
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

from src.agents.prompts.team.orchestrator_prompts import CI_FAILURE_USER_PROMPT
from src.agents.team.orchestrator_agent import OrchestratorAgent
from src.agents.team.publisher_agent import PublisherAgent
from src.api.schemas.webhook_schemas import GitHubWorkflowRunPayload
from src.config import Settings, get_settings
from src.config.constants import (
    WS_EVENT_PIPELINE_FAILED,
    WS_EVENT_TASK_STATUS_CHANGED,
)
from src.db.models.task import Task, TaskStatus
from src.db.queries.task_query import TaskRepository
from src.db.session import Database, db
from src.utils.broadcaster import broadcaster
from src.utils.exceptions import WebhookRetryLater


# Branch names created by Developer agent follow this pattern:
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

    async def process_github_event(
        self,
        *,
        raw_body: bytes,
        signature: str,
        event_type: str,
        session: AsyncSession,
    ) -> dict[str, str]:
        """Verify signature, parse payload, and route the event.

        Returns a dict with 'status' key ('ok' or 'error').
        Raises AuthenticationError for missing/invalid signatures.
        """
        from src.utils.exceptions import AuthenticationError

        if not signature:
            raise AuthenticationError("Missing X-Hub-Signature-256 header.")

        if not self.verify_signature(payload=raw_body, signature=signature):
            raise AuthenticationError("Invalid webhook signature.")

        self._logger.info("webhook.received", github_event=event_type)

        if event_type == "workflow_run":
            payload = GitHubWorkflowRunPayload.model_validate_json(raw_body)
            await self.handle_workflow_run(session=session, payload=payload)
        elif event_type == "ping":
            self._logger.info("webhook.ping")
        else:
            self._logger.debug("webhook.unhandled_event", github_event=event_type)

        return {"status": "ok"}

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

        # Race guard: GitHub Actions can fire workflow_run events before the
        # initial pipeline finished saving task.state. Returning 503 asks
        # GitHub to redeliver after a short backoff — by then the task should
        # have transitioned to AWAITING_CI and we can act on it.
        if task.status == TaskStatus.RUNNING:
            log.info("webhook.task_still_running_will_retry")
            raise WebhookRetryLater(
                "Initial pipeline is still running; retry once it transitions "
                "to AWAITING_CI.",
            )

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

            # Transition to FIXING and spawn the fix handler in the background.
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
        """Background coroutine that re-runs the agent pipeline against a CI failure.

        Owns its own DB session so the request session closing does not
        affect it. Reads ``task.description`` and ``task.agent_id`` fresh
        from the database, formats the orchestrator user message via
        ``CI_FAILURE_USER_PROMPT`` (so the orchestrator delegates to the
        ``devops`` sub-agent), and runs ``OrchestratorAgent`` →
        ``PublisherAgent``. Final task status is ``AWAITING_CI`` (new
        commit pushed to the existing PR), ``NEEDS_HUMAN`` (no diffs
        produced), or ``FAILED`` (exception).
        """
        next_attempt = attempt + 1
        log = self._logger.bind(task_id=str(task_id), attempt=next_attempt)
        log.info("devops_fix.started")

        try:
            async with self._database.session_scope() as session:
                task = await TaskRepository(session).get(
                    user_id=user_id, task_id=task_id
                )
                base_description = task.description
                agent_id = task.agent_id

            state: dict[str, Any] = {
                **task_state,
                "task_id": str(task_id),
                "user_id": user_id,
                "agent_id": str(agent_id),
                "attempt": next_attempt,
                "description": CI_FAILURE_USER_PROMPT.format(
                    run_id=ci_run_id,
                    repo_full_name=ci_repo_full_name,
                    attempt=next_attempt,
                    description=base_description,
                ),
            }

            for agent_class in (OrchestratorAgent, PublisherAgent):
                agent = agent_class()
                result = await agent(state)
                state = {**state, **result}

            pr_urls = state.get("pr_urls") or {}
            if pr_urls:
                new_status = TaskStatus.AWAITING_CI
                error_message: str | None = None
            else:
                new_status = TaskStatus.NEEDS_HUMAN
                error_message = (
                    "Automated fix produced no changes. "
                    "CI logs require manual review."
                )

            async with self._database.session_scope() as session:
                repo = TaskRepository(session)
                task = await repo.get(user_id=user_id, task_id=task_id)
                await repo.update_status(
                    task=task,
                    status=new_status,
                    attempt=next_attempt,
                    error_message=error_message,
                    pr_urls_patch=pr_urls or None,
                )

            await broadcaster.publish(task_id, {
                "name": WS_EVENT_TASK_STATUS_CHANGED,
                "agent": None,
                "payload": {"status": new_status.value},
                "occurred_at": "",
            })
            await broadcaster.close_task(task_id)
            log.info(
                "devops_fix.finished",
                status=new_status.value,
                pr_count=len(pr_urls),
            )

        except Exception as exc:
            log.exception("devops_fix.failed", error=str(exc))
            await broadcaster.publish(task_id, {
                "name": WS_EVENT_PIPELINE_FAILED,
                "agent": None,
                "payload": {"error": str(exc)[:2000]},
                "occurred_at": "",
            })
            await broadcaster.close_task(task_id)
            try:
                async with self._database.session_scope() as session:
                    repo = TaskRepository(session)
                    task = await repo.get(user_id=user_id, task_id=task_id)
                    await repo.update_status(
                        task=task,
                        status=TaskStatus.FAILED,
                        attempt=next_attempt,
                        error_message=str(exc)[:2000],
                    )
            except Exception:
                self._logger.exception("devops_fix.status_update_failed")


