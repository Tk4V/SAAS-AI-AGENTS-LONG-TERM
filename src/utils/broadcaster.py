"""Cross-process pub/sub for pipeline events, keyed by task ID.

Backed by Redis so events published from a pipeline coroutine in one
worker reach WebSocket subscribers in any other worker. The interface
is intentionally the same as the previous in-process implementation:

    queue, unsubscribe = await broadcaster.subscribe(task_id)
    while True:
        event = await queue.get()
        if event is None:
            break  # task closed
        ...
    await unsubscribe()

Internally each ``subscribe`` opens a dedicated Redis pubsub connection
and pumps messages into the returned ``asyncio.Queue``. ``publish`` is a
single Redis PUBLISH. The sentinel ``None`` is sent as a literal JSON
``null`` on the channel and signals end-of-stream.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog

from src.clients import clients

PipelineEvent = dict[str, Any]
_END = "__end__"  # marker payload meaning "task is done, close subscribers"


def _channel(task_id: UUID) -> str:
    return f"clyde:task:{task_id}"


class EventBroadcaster:
    def __init__(self) -> None:
        self._logger = structlog.get_logger("clyde.broadcaster")

    async def subscribe(
        self, task_id: UUID
    ) -> tuple[asyncio.Queue[PipelineEvent | None], Callable[[], Awaitable[None]]]:
        """Subscribe to events for a task.

        Returns a queue that yields each published event, plus an async
        unsubscribe callable. The queue ends with ``None`` once
        ``close_task`` has been called for this task.
        """
        queue: asyncio.Queue[PipelineEvent | None] = asyncio.Queue()
        pubsub = clients.redis.pubsub()
        await pubsub.subscribe(_channel(task_id))

        stopped = asyncio.Event()

        async def _pump() -> None:
            try:
                while not stopped.is_set():
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if msg is None:
                        continue
                    raw = msg.get("data")
                    if raw is None:
                        continue
                    if raw == _END:
                        await queue.put(None)
                        return
                    try:
                        await queue.put(json.loads(raw))
                    except json.JSONDecodeError:
                        self._logger.warning(
                            "subscriber.bad_payload", task_id=str(task_id)
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("subscriber.pump_failed", task_id=str(task_id))

        task = asyncio.create_task(_pump(), name=f"broadcaster-pump-{task_id}")
        self._logger.debug("subscriber.added", task_id=str(task_id))

        async def unsubscribe() -> None:
            stopped.set()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await pubsub.unsubscribe(_channel(task_id))
                await pubsub.aclose()
            except Exception:
                self._logger.exception(
                    "subscriber.close_failed", task_id=str(task_id)
                )
            self._logger.debug("subscriber.removed", task_id=str(task_id))

        return queue, unsubscribe

    async def publish(self, task_id: UUID, event: PipelineEvent) -> None:
        """Fan out an event to every worker subscribed to this task."""
        await clients.redis.publish(_channel(task_id), json.dumps(event))

    async def close_task(self, task_id: UUID) -> None:
        """Send the end-of-stream sentinel so subscribers can finish gracefully."""
        await clients.redis.publish(_channel(task_id), _END)
        self._logger.debug("task.closed", task_id=str(task_id))

    @classmethod
    def create(cls) -> "EventBroadcaster":
        return cls()


broadcaster = EventBroadcaster()
