"""Post-turn hook that fires after every completed chat turn.

The orchestrator's ``SDKChatSession`` invokes this callback once per
agent turn. Its job:

1. Switch the task status to ``PUBLISHING`` and emit ``publish.started``.
2. Call ``PublisherAgent.publish_turn`` for each repo the task is
   working in.
3. If the publisher reports ``did_publish=False`` (no diffs), skip
   silently — turn was a pure conversation.
4. Persist ``branch_name`` and ``pr_url`` on ``task.state`` so the next
   turn knows to push onto the same branch instead of opening a new PR.
5. Emit ``publish.finished`` / ``publish.failed`` so the UI can show
   per-PR feedback.

The callback never raises — failures are logged and broadcast. The chat
session itself decides whether to keep going.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from src.config.constants import (
    WS_EVENT_PUBLISH_FAILED,
    WS_EVENT_PUBLISH_FINISHED,
    WS_EVENT_PUBLISH_STARTED,
    WS_EVENT_TASK_STATUS_CHANGED,
)
from src.db.models.task import TaskStatus
from src.db.queries.task_query import TaskRepository
from src.db.session import db
from src.utils.broadcaster import broadcaster

if TYPE_CHECKING:
    from src.agents.chat.session import SDKChatSession, TurnResult

_logger = structlog.get_logger("clyde.chat.turn_handler")


def build_post_turn_callback(*, task_id: UUID, user_id: int):  # type: ignore[no-untyped-def]
    """Return a callback bound to a specific task/user, ready to drop
    into ``SDKChatSession(post_turn_callback=...)``."""

    async def _on_turn_finished(
        session: "SDKChatSession", result: "TurnResult"
    ) -> None:
        log = _logger.bind(
            task_id=str(task_id),
            user_id=user_id,
            turn=result.turn_index,
        )
        try:
            await _publish_turn(
                task_id=task_id,
                user_id=user_id,
                turn=result,
                log=log,
            )
        except Exception as exc:
            log.exception("post_turn.unhandled_error", error=str(exc))
            await broadcaster.publish(task_id, {
                "name": WS_EVENT_PUBLISH_FAILED,
                "agent": "publisher",
                "payload": {"error": str(exc)[:500]},
                "occurred_at": _now_iso(),
            })

    return _on_turn_finished


async def _publish_turn(
    *,
    task_id: UUID,
    user_id: int,
    turn: "TurnResult",
    log: structlog.stdlib.BoundLogger,
) -> None:
    # Pull the task so we can find the workspace + repo metadata the
    # orchestrator stashed in state when it cloned everything.
    async with db.session_scope() as session:
        repo = TaskRepository(session)
        task = await repo.get(user_id=user_id, task_id=task_id)
        state: dict[str, Any] = dict(task.state or {})

    repos: list[dict[str, Any]] = state.get("repos") or []
    workspace_str = state.get("workspace_path")
    if not repos or not workspace_str:
        # Pure Jira / chat-only task — nothing to publish.
        log.info("post_turn.no_workspace")
        return
    workspace_path = Path(workspace_str)

    # Branch / PR tracking lives in state so it survives across turns.
    branches: dict[str, str] = dict(state.get("branch_names") or {})
    pr_urls: dict[str, str] = dict(state.get("pr_urls") or {})

    await _set_status(task_id=task_id, user_id=user_id, status=TaskStatus.PUBLISHING)
    await broadcaster.publish(task_id, {
        "name": WS_EVENT_PUBLISH_STARTED,
        "agent": "publisher",
        "payload": {"turn": turn.turn_index},
        "occurred_at": _now_iso(),
    })

    from src.agents.team.publisher_agent import PublisherAgent
    publisher = PublisherAgent()

    any_published = False
    for repo_meta in repos:
        repo_name = repo_meta.get("name") or ""
        if not repo_name:
            continue
        try:
            result = await publisher.publish_turn(
                workspace_path=workspace_path,
                repo_meta=repo_meta,
                user_id=user_id,
                task_id=str(task_id),
                task_description=state.get("description") or "",
                branch_name=branches.get(repo_name),
                prior_pr_url=pr_urls.get(repo_name),
                commit_summary=turn.assistant_text,
            )
        except Exception as exc:
            log.exception(
                "post_turn.publish_failed",
                repo=repo_name,
                error=str(exc),
            )
            await broadcaster.publish(task_id, {
                "name": WS_EVENT_PUBLISH_FAILED,
                "agent": "publisher",
                "payload": {"repo": repo_name, "error": str(exc)[:500]},
                "occurred_at": _now_iso(),
            })
            continue

        if not result.get("did_publish"):
            log.info(
                "post_turn.no_changes",
                repo=repo_name,
                reason=result.get("reason"),
            )
            continue

        any_published = True
        new_branch = result.get("branch_name")
        new_pr = result.get("pr_url")
        if new_branch:
            branches[repo_name] = new_branch
        if new_pr:
            pr_urls[repo_name] = new_pr

        await broadcaster.publish(task_id, {
            "name": WS_EVENT_PUBLISH_FINISHED,
            "agent": "publisher",
            "payload": {
                "repo": repo_name,
                "branch": new_branch,
                "pr_url": new_pr,
                "turn": turn.turn_index,
            },
            "occurred_at": _now_iso(),
        })

    # Persist the updated branch / PR maps regardless of did_publish so
    # we don't repeatedly hit the GitHub PR-create endpoint on retries.
    async with db.session_scope() as session:
        repo = TaskRepository(session)
        task = await repo.get(user_id=user_id, task_id=task_id)
        await repo.update_status(
            task=task,
            status=task.status,  # status reset happens via the session
            state_patch={
                "branch_names": branches,
            },
            pr_urls_patch=pr_urls or None,
        )

    log.info("post_turn.completed", any_published=any_published)


async def _set_status(
    *, task_id: UUID, user_id: int, status: TaskStatus
) -> None:
    async with db.session_scope() as session:
        repo = TaskRepository(session)
        task = await repo.get(user_id=user_id, task_id=task_id)
        await repo.update_status(task=task, status=status)
    await broadcaster.publish(task_id, {
        "name": WS_EVENT_TASK_STATUS_CHANGED,
        "agent": None,
        "payload": {"status": status.value},
        "occurred_at": _now_iso(),
    })


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
