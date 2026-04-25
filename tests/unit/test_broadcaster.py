"""Tests for the EventBroadcaster in-memory pub/sub."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from src.utils.broadcaster import EventBroadcaster


class TestEventBroadcaster:
    """Validate subscribe/publish flow, sentinel delivery, and cleanup."""

    async def test_subscribe_and_publish(self) -> None:
        broadcaster = EventBroadcaster()
        task_id = uuid4()

        queue, unsub = broadcaster.subscribe(task_id)
        event = {"name": "test.event", "agent": None, "payload": {}, "occurred_at": ""}
        await broadcaster.publish(task_id, event)

        received = queue.get_nowait()
        assert received["name"] == "test.event"
        unsub()

    async def test_multiple_subscribers(self) -> None:
        """All subscribers should receive the same event."""
        broadcaster = EventBroadcaster()
        task_id = uuid4()

        q1, unsub1 = broadcaster.subscribe(task_id)
        q2, unsub2 = broadcaster.subscribe(task_id)

        event = {"name": "multi.event", "agent": "developer", "payload": {}, "occurred_at": ""}
        await broadcaster.publish(task_id, event)

        assert q1.get_nowait()["name"] == "multi.event"
        assert q2.get_nowait()["name"] == "multi.event"

        unsub1()
        unsub2()

    async def test_close_task_sends_sentinel(self) -> None:
        """close_task should push None (the end-of-stream marker) to all subscribers."""
        broadcaster = EventBroadcaster()
        task_id = uuid4()

        queue, _unsub = broadcaster.subscribe(task_id)
        await broadcaster.close_task(task_id)

        sentinel = queue.get_nowait()
        assert sentinel is None

    async def test_unsubscribe_removes_queue(self) -> None:
        """After unsubscribing, publish should not put events in the removed queue."""
        broadcaster = EventBroadcaster()
        task_id = uuid4()

        queue, unsub = broadcaster.subscribe(task_id)
        unsub()

        event = {"name": "after.unsub", "agent": None, "payload": {}, "occurred_at": ""}
        await broadcaster.publish(task_id, event)

        assert queue.empty(), "Unsubscribed queue should not receive events"

    async def test_publish_to_unknown_task_is_noop(self) -> None:
        """Publishing to a task with no subscribers should not raise."""
        broadcaster = EventBroadcaster()
        event = {"name": "orphan.event", "agent": None, "payload": {}, "occurred_at": ""}
        await broadcaster.publish(uuid4(), event)

    async def test_close_task_cleans_up_subscribers(self) -> None:
        """After close_task, the internal subscriber list should be empty."""
        broadcaster = EventBroadcaster()
        task_id = uuid4()

        _queue, _unsub = broadcaster.subscribe(task_id)
        await broadcaster.close_task(task_id)

        q2, unsub2 = broadcaster.subscribe(task_id)
        event = {"name": "new.event", "agent": None, "payload": {}, "occurred_at": ""}
        await broadcaster.publish(task_id, event)
        assert q2.get_nowait()["name"] == "new.event"
        unsub2()
