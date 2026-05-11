"""Unit tests for ``TaskInputQueue`` — Redis client mocked.

The real Redis is exercised in the integration suite. Here we just want
to verify the module's contract: payload encoding, queue-cap behaviour,
timeout semantics, and that ``clear`` issues the right DELETE.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.utils.task_input_queue import TaskInputQueue, _MAX_QUEUE_DEPTH


class FakeRedis:
    """Tiny in-memory stand-in just for these tests."""

    def __init__(self) -> None:
        self.store: dict[str, list[str]] = {}
        self.deletes: list[str] = []

    async def llen(self, key: str) -> int:
        return len(self.store.get(key, []))

    async def rpush(self, key: str, value: str) -> int:
        self.store.setdefault(key, []).append(value)
        return len(self.store[key])

    async def blpop(self, key: str, timeout: int) -> tuple[str, str] | None:
        items = self.store.get(key, [])
        if items:
            return key, items.pop(0)
        return None  # mimic timeout

    async def delete(self, key: str) -> int:
        self.deletes.append(key)
        existed = key in self.store
        self.store.pop(key, None)
        return 1 if existed else 0


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def queue_with_fake(fake_redis: FakeRedis) -> Any:
    with patch("src.utils.task_input_queue.clients") as clients_mock:
        clients_mock.redis = fake_redis
        yield TaskInputQueue()


class TestTaskInputQueue:
    async def test_push_then_wait_round_trip(
        self, queue_with_fake: TaskInputQueue
    ) -> None:
        task_id = uuid4()
        ok = await queue_with_fake.push(task_id=task_id, content="hello agent")
        assert ok is True

        msg = await queue_with_fake.wait_for_message(
            task_id=task_id, timeout_sec=1
        )
        assert msg == "hello agent"

    async def test_wait_returns_none_on_timeout(
        self, queue_with_fake: TaskInputQueue
    ) -> None:
        task_id = uuid4()
        msg = await queue_with_fake.wait_for_message(task_id=task_id, timeout_sec=1)
        assert msg is None

    async def test_drops_when_queue_full(
        self, queue_with_fake: TaskInputQueue, fake_redis: FakeRedis
    ) -> None:
        task_id = uuid4()
        # Fill to cap; then the next push must be refused.
        key = f"clyde:task:{task_id}:input"
        fake_redis.store[key] = [
            json.dumps({"content": f"msg-{i}"}) for i in range(_MAX_QUEUE_DEPTH)
        ]
        ok = await queue_with_fake.push(task_id=task_id, content="overflow")
        assert ok is False
        assert len(fake_redis.store[key]) == _MAX_QUEUE_DEPTH  # didn't grow

    async def test_clear_deletes_key(
        self, queue_with_fake: TaskInputQueue, fake_redis: FakeRedis
    ) -> None:
        task_id = uuid4()
        await queue_with_fake.push(task_id=task_id, content="x")
        await queue_with_fake.clear(task_id=task_id)
        assert fake_redis.deletes == [f"clyde:task:{task_id}:input"]

    async def test_bad_payload_returns_none(
        self, queue_with_fake: TaskInputQueue, fake_redis: FakeRedis
    ) -> None:
        task_id = uuid4()
        key = f"clyde:task:{task_id}:input"
        fake_redis.store[key] = ["not json {{{"]
        msg = await queue_with_fake.wait_for_message(task_id=task_id, timeout_sec=1)
        assert msg is None
