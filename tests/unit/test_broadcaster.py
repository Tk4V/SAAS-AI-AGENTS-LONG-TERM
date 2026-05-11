"""Tests for ``EventBroadcaster`` — the Redis pub/sub fan-out for task events.

We don't talk to a real Redis here. ``FakeRedis`` is a minimal stand-in
that implements just the ``pubsub`` / ``publish`` / ``aclose`` calls the
broadcaster makes, with in-process wiring between publishers and pubsub
listeners so subscribe/publish round-trips work synchronously in test
order.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.utils.broadcaster import EventBroadcaster, _END


class _FakePubSub:
    def __init__(self, fake: "_FakeRedis") -> None:
        self._fake = fake
        self._channels: set[str] = set()
        self._messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._closed = False

    async def subscribe(self, channel: str) -> None:
        self._channels.add(channel)
        self._fake._listeners.setdefault(channel, []).append(self)

    async def unsubscribe(self, channel: str) -> None:
        self._channels.discard(channel)
        listeners = self._fake._listeners.get(channel, [])
        if self in listeners:
            listeners.remove(self)

    async def get_message(
        self, ignore_subscribe_messages: bool = True, timeout: float | None = None
    ) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(
                self._messages.get(), timeout=timeout or 0.01
            )
        except asyncio.TimeoutError:
            return None

    async def aclose(self) -> None:
        self._closed = True

    def deliver(self, raw: str) -> None:
        self._messages.put_nowait({"type": "message", "data": raw})


class _FakeRedis:
    def __init__(self) -> None:
        self._listeners: dict[str, list[_FakePubSub]] = {}

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self)

    async def publish(self, channel: str, payload: str) -> int:
        listeners = self._listeners.get(channel, [])
        for ps in listeners:
            ps.deliver(payload)
        return len(listeners)


@pytest.fixture
def fake_redis() -> _FakeRedis:
    return _FakeRedis()


@pytest.fixture
def broadcaster_with_fake(fake_redis: _FakeRedis) -> Any:
    with patch("src.utils.broadcaster.clients") as clients_mock:
        clients_mock.redis = fake_redis
        yield EventBroadcaster()


class TestEventBroadcaster:
    async def test_subscribe_and_publish(
        self, broadcaster_with_fake: EventBroadcaster
    ) -> None:
        task_id = uuid4()
        queue, unsub = await broadcaster_with_fake.subscribe(task_id)
        try:
            event = {"name": "test.event", "agent": None, "payload": {}, "occurred_at": ""}
            await broadcaster_with_fake.publish(task_id, event)
            received = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert received["name"] == "test.event"
        finally:
            await unsub()

    async def test_multiple_subscribers_each_get_a_copy(
        self, broadcaster_with_fake: EventBroadcaster
    ) -> None:
        task_id = uuid4()
        q1, unsub1 = await broadcaster_with_fake.subscribe(task_id)
        q2, unsub2 = await broadcaster_with_fake.subscribe(task_id)
        try:
            event = {"name": "multi", "agent": "orchestrator", "payload": {}, "occurred_at": ""}
            await broadcaster_with_fake.publish(task_id, event)
            assert (await asyncio.wait_for(q1.get(), timeout=2.0))["name"] == "multi"
            assert (await asyncio.wait_for(q2.get(), timeout=2.0))["name"] == "multi"
        finally:
            await unsub1()
            await unsub2()

    async def test_close_task_sends_sentinel(
        self, broadcaster_with_fake: EventBroadcaster
    ) -> None:
        task_id = uuid4()
        queue, unsub = await broadcaster_with_fake.subscribe(task_id)
        try:
            await broadcaster_with_fake.close_task(task_id)
            sentinel = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert sentinel is None
        finally:
            await unsub()

    async def test_unsubscribe_stops_delivery(
        self, broadcaster_with_fake: EventBroadcaster
    ) -> None:
        task_id = uuid4()
        queue, unsub = await broadcaster_with_fake.subscribe(task_id)
        await unsub()
        await broadcaster_with_fake.publish(task_id, {"name": "after.unsub"})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.2)

    async def test_publish_to_unknown_task_is_noop(
        self, broadcaster_with_fake: EventBroadcaster
    ) -> None:
        # No subscribers → publish returns 0 from FakeRedis, nothing raises.
        await broadcaster_with_fake.publish(uuid4(), {"name": "orphan"})

    async def test_end_sentinel_value(self) -> None:
        # Sanity check the module's contract — the sentinel is a marker
        # string, not the literal Python None, so it survives JSON round
        # trips through Redis.
        assert isinstance(_END, str)
