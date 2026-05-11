"""Cross-process permission gate for human-in-the-loop tool approval.

Coordinates between the pipeline coroutine (which blocks waiting for the
user) and whichever process handles the WebSocket message that resolves
the approval. The two sides never share Python state — they meet on Redis.

Flow per approval:

1. ``register(approval_id)`` is called by the pipeline. It opens a pubsub
   subscription on ``clyde:approval:wake:{id}`` *before* doing anything
   else, so a resolve published in the millisecond after registration is
   not lost.
2. Pipeline awaits ``wait_for_decision(approval_id)``.
3. WebSocket handler (possibly in another worker) calls ``resolve(...)``,
   which writes the decision into ``clyde:approval:{id}`` (24h TTL) and
   PUBLISHes a wake notification on the pubsub channel.
4. ``wait_for_decision`` reads the wake message, fetches the decision
   from the key, and returns ``(approved, payload)``.

Multi-worker safe — works under any uvicorn / gunicorn topology as long
as every worker shares the same Redis instance.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import structlog

from src.clients import clients

_logger = structlog.get_logger("clyde.permission_gate")

# 24 hours — long enough that a user can step away from the UI and still
# resolve a pending approval, but bounded so we do not leak Redis keys
# forever if the pipeline coroutine dies before consuming the decision.
_DECISION_TTL_SEC = 60 * 60 * 24


def _decision_key(approval_id: UUID) -> str:
    return f"clyde:approval:{approval_id}"


def _wake_channel(approval_id: UUID) -> str:
    return f"clyde:approval:wake:{approval_id}"


class _PendingApproval:
    """Subscription handle returned by ``register`` and consumed by
    ``wait_for_decision``. Holds the open pubsub object until the decision
    arrives so we do not race against PUBLISH."""

    def __init__(self, approval_id: UUID, pubsub: Any) -> None:
        self.approval_id = approval_id
        self.pubsub = pubsub


_pending: dict[UUID, _PendingApproval] = {}


async def register(*, task_id: UUID, approval_id: UUID) -> None:
    """Open the wake subscription for an approval before publishing the
    request event to the user. ``task_id`` is kept in the signature so
    callers do not need to change when we add per-task indexing later.
    """
    del task_id  # not needed yet; see docstring
    pubsub = clients.redis.pubsub()
    await pubsub.subscribe(_wake_channel(approval_id))
    _pending[approval_id] = _PendingApproval(approval_id, pubsub)
    _logger.debug("approval.registered", approval_id=str(approval_id))


async def resolve(
    *,
    approval_id: UUID,
    approved: bool,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Persist the decision and wake any coroutine waiting on it.

    Returns True when at least one subscriber received the wake notification,
    False otherwise. False is informational — the decision is still stored
    and ``wait_for_decision`` will pick it up if a late subscriber appears.
    """
    decision = {"approved": approved, "payload": payload or {}}
    await clients.redis.set(
        _decision_key(approval_id),
        json.dumps(decision),
        ex=_DECISION_TTL_SEC,
    )
    receivers = await clients.redis.publish(
        _wake_channel(approval_id), "1"
    )
    _logger.info(
        "approval.resolved",
        approval_id=str(approval_id),
        approved=approved,
        receivers=receivers,
    )
    return receivers > 0


async def wait_for_decision(
    *, approval_id: UUID, timeout_sec: float | None = None
) -> tuple[bool, dict[str, Any]]:
    """Block until the user resolves this approval and return their
    decision. Must be preceded by a call to ``register``.

    The decision may already be in Redis when we start waiting (resolve
    raced ahead of us); we check the key first and only sleep on the
    pubsub channel if it is not yet set.
    """
    pending = _pending.pop(approval_id, None)
    if pending is None:
        raise RuntimeError(
            f"wait_for_decision called without prior register for {approval_id}"
        )

    try:
        cached = await clients.redis.get(_decision_key(approval_id))
        if cached is not None:
            return _parse_decision(cached)

        async def _next_message() -> None:
            while True:
                msg = await pending.pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=None
                )
                if msg is not None:
                    return

        if timeout_sec is not None:
            await asyncio.wait_for(_next_message(), timeout=timeout_sec)
        else:
            await _next_message()

        cached = await clients.redis.get(_decision_key(approval_id))
        if cached is None:
            raise RuntimeError(
                f"Wake fired for approval {approval_id} but no decision in Redis"
            )
        return _parse_decision(cached)
    finally:
        try:
            await pending.pubsub.unsubscribe(_wake_channel(approval_id))
            await pending.pubsub.aclose()
        except Exception:
            _logger.exception("approval.pubsub_close_failed", approval_id=str(approval_id))


def _parse_decision(raw: str) -> tuple[bool, dict[str, Any]]:
    data = json.loads(raw)
    return bool(data.get("approved", False)), dict(data.get("payload") or {})


async def cleanup(*, approval_id: UUID) -> None:
    """Remove the persisted decision once the pipeline has consumed it.
    Optional — Redis TTL would expire it anyway, but cleaning up keeps the
    keyspace tidy and visible in redis-cli."""
    await clients.redis.delete(_decision_key(approval_id))


def cleanup_task(task_id: UUID) -> None:  # noqa: ARG001
    """No-op shim retained for callers that used the old in-process API.

    The previous implementation tracked pending approvals per task in
    process memory and needed an explicit per-task sweep when the task
    finished. The Redis-backed implementation gives every decision key a
    TTL, so a missed cleanup expires harmlessly on its own. We keep the
    function so existing call sites in agents/ keep working without an
    awaited refactor; new code should call ``cleanup(approval_id=...)``
    after consuming a single decision.
    """
    return None
