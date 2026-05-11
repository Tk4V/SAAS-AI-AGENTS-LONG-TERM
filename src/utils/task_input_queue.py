"""Redis-backed FIFO queue for inbound user messages on a task chat session.

The WebSocket handler pushes user messages onto the queue; the chat
session coroutine (potentially running in another worker) pops them via
BLPOP and feeds each one into the live SDK session as a new user-turn.

Why a queue and not a pub/sub channel: the chat session has exactly one
consumer per task and we want messages to survive transient gaps when
the consumer is mid-turn. A LIST gives us at-most-once delivery with a
real backlog; pub/sub would drop a message arriving while the consumer
isn't blocked on it. Redis stream is overkill for one consumer.

Each task has its own list key ``clyde:task:{id}:input``. The session
coroutine ``clear()``s its key on close.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import structlog

from src.clients import clients

_logger = structlog.get_logger("clyde.task_input_queue")

# Hard cap to stop a runaway user from blowing up Redis memory if the
# session consumer is wedged. Excess messages are dropped silently and
# logged — by then the user is clearly trying to spam and the session
# is likely about to time out anyway.
_MAX_QUEUE_DEPTH = 200


def _key(task_id: UUID) -> str:
    return f"clyde:task:{task_id}:input"


class TaskInputQueue:
    """Per-task FIFO of inbound user messages, persisted in Redis."""

    async def push(self, *, task_id: UUID, content: str) -> bool:
        """Append a user message to the back of the task's queue.

        Returns ``True`` if the message was enqueued, ``False`` if the
        queue was at its cap and the message was dropped.
        """
        key = _key(task_id)
        depth = await clients.redis.llen(key)
        if depth >= _MAX_QUEUE_DEPTH:
            _logger.warning(
                "input_queue.full", task_id=str(task_id), depth=depth
            )
            return False
        await clients.redis.rpush(key, json.dumps({"content": content}))
        return True

    async def wait_for_message(
        self, *, task_id: UUID, timeout_sec: float
    ) -> str | None:
        """Block until a message arrives or the timeout elapses.

        Returns the message text, or ``None`` on timeout. ``BLPOP`` makes
        this efficient even with many idle queues; no polling required.
        """
        # redis-py expects an int for BLPOP timeout; 0 = block forever
        # which we never want for chat sessions.
        timeout = max(1, int(round(timeout_sec)))
        result = await clients.redis.blpop(_key(task_id), timeout=timeout)
        if result is None:
            return None
        # BLPOP returns (key, value); when decode_responses=True both are str.
        _, raw = result
        try:
            payload: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            _logger.warning(
                "input_queue.bad_payload",
                task_id=str(task_id),
                raw_preview=raw[:200],
            )
            return None
        return str(payload.get("content", ""))

    async def clear(self, *, task_id: UUID) -> None:
        """Drop any pending messages for this task. Called when the
        session closes so a future re-use of the same task_id (rare but
        possible during testing) does not pick up stale input."""
        await clients.redis.delete(_key(task_id))

    async def depth(self, *, task_id: UUID) -> int:
        """How many messages are currently queued — for diagnostics."""
        return int(await clients.redis.llen(_key(task_id)))


task_input_queue = TaskInputQueue()
