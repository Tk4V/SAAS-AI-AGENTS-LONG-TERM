"""In-memory pub/sub for pipeline events, keyed by task ID.

When a pipeline emits an event the background runner calls
``broadcaster.publish(task_id, event)`` and every WebSocket client that
previously called ``broadcaster.subscribe(task_id)`` receives a copy. The
sentinel value ``None`` signals end-of-stream so clients know the pipeline
is done and can close their connection gracefully.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID

import structlog

PipelineEvent = dict[str, Any]


class EventBroadcaster:
    """In-memory pub/sub for pipeline events per task.

    WebSocket handlers subscribe via ``subscribe(task_id)`` which returns
    an async queue plus an unsubscribe callable. Pipeline runners publish
    events via ``publish(task_id, event)``.
    """

    def __init__(self) -> None:
        """Initialise the broadcaster with an empty subscriber registry."""
        self._subscribers: dict[UUID, list[asyncio.Queue[PipelineEvent | None]]] = {}
        self._logger = structlog.get_logger("clyde.broadcaster")

    def subscribe(
        self, task_id: UUID
    ) -> tuple[asyncio.Queue[PipelineEvent | None], Callable[[], None]]:
        """Register a new subscriber for the given task.

        Returns:
            A (queue, unsubscribe) tuple. Read events from the queue,
            call unsubscribe when done.
        """
        queue: asyncio.Queue[PipelineEvent | None] = asyncio.Queue()
        bucket = self._subscribers.setdefault(task_id, [])
        bucket.append(queue)
        self._logger.debug("subscriber.added", task_id=str(task_id), count=len(bucket))

        def unsubscribe() -> None:
            """Remove this queue from the subscriber list."""
            try:
                bucket.remove(queue)
            except ValueError:
                pass
            if not bucket and task_id in self._subscribers:
                del self._subscribers[task_id]
            self._logger.debug("subscriber.removed", task_id=str(task_id))

        return queue, unsubscribe

    async def publish(self, task_id: UUID, event: PipelineEvent) -> None:
        """Fan-out an event to every subscriber watching this task."""
        for queue in self._subscribers.get(task_id, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._logger.warning("subscriber.queue_full", task_id=str(task_id))

    async def close_task(self, task_id: UUID) -> None:
        """Send the sentinel ``None`` to all subscribers and clean up.

        Called once when the pipeline finishes so every connected
        client knows the stream is over.
        """
        for queue in self._subscribers.pop(task_id, []):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._logger.debug("task.closed", task_id=str(task_id))

    @classmethod
    def create(cls) -> "EventBroadcaster":
        """Factory method to create a new broadcaster instance."""
        return cls()


broadcaster = EventBroadcaster()
