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
from uuid import UUID

import structlog

from src.engine.state import PipelineEvent


class EventBroadcaster:
    """In-memory pub/sub for pipeline events per task.

    When a pipeline emits an event, it calls broadcaster.publish(task_id, event).
    WebSocket handlers subscribe via broadcaster.subscribe(task_id) which returns
    an async queue plus an unsubscribe callable. Multiple WS clients can subscribe
    to the same task concurrently.
    """

    def __init__(self) -> None:
        self._subscribers: dict[UUID, list[asyncio.Queue[PipelineEvent | None]]] = {}
        self._logger = structlog.get_logger("clyde.broadcaster")

    def subscribe(self, task_id: UUID) -> tuple[asyncio.Queue[PipelineEvent | None], Callable[[], None]]:
        """Register a new subscriber for the given task.

        Returns a (queue, unsubscribe) pair. The caller reads events from the
        queue and calls unsubscribe when done (e.g. client disconnected).
        """
        queue: asyncio.Queue[PipelineEvent | None] = asyncio.Queue()
        bucket = self._subscribers.setdefault(task_id, [])
        bucket.append(queue)
        self._logger.debug("subscriber.added", task_id=str(task_id), count=len(bucket))

        def unsubscribe() -> None:
            try:
                bucket.remove(queue)
            except ValueError:
                pass
            # Clean up the task entry when no subscribers remain.
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
                self._logger.warning(
                    "subscriber.queue_full",
                    task_id=str(task_id),
                )

    async def close_task(self, task_id: UUID) -> None:
        """Send the sentinel None to all subscribers and remove the task bucket.

        Called once when the pipeline finishes (success or failure) so every
        connected client knows the stream is over.
        """
        for queue in self._subscribers.pop(task_id, []):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._logger.debug("task.closed", task_id=str(task_id))


broadcaster = EventBroadcaster()
