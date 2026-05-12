"""Unit tests for ``S3SessionStore`` — boto3 client mocked.

The store is exercised against a tiny fake S3 that implements just the
operations our adapter touches (``get_object``, ``put_object``,
``delete_object``, ``delete_objects``, ``get_paginator``). This keeps
the test suite dependency-free and fast; the real S3 path is covered by
the integration suite once that's running.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
from claude_agent_sdk.types import SessionKey

from src.agents.chat.s3_session_store import S3SessionStore, _S3NotFound


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            err = ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "not there"}},
                "GetObject",
            )
            raise err
        return {"Body": _FakeBody(self.objects[Key])}

    def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, ContentType: str = ""
    ) -> dict[str, Any]:
        self.objects[Key] = Body
        return {}

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.objects.pop(Key, None)
        self.deleted.append(Key)
        return {}

    def delete_objects(
        self, *, Bucket: str, Delete: dict[str, Any]
    ) -> dict[str, Any]:
        for entry in Delete.get("Objects", []):
            key = entry["Key"]
            self.objects.pop(key, None)
            self.deleted.append(key)
        return {}

    def get_paginator(self, op: str) -> "_FakePaginator":
        assert op == "list_objects_v2"
        return _FakePaginator(self)


class _FakePaginator:
    def __init__(self, fake: _FakeS3) -> None:
        self._fake = fake

    def paginate(self, *, Bucket: str, Prefix: str) -> Any:
        matches = [
            {"Key": k} for k in self._fake.objects.keys() if k.startswith(Prefix)
        ]
        yield {"Contents": matches} if matches else {}


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


@pytest.fixture
def fake_s3() -> _FakeS3:
    return _FakeS3()


@pytest.fixture
def store(fake_s3: _FakeS3) -> Any:
    with patch("src.agents.chat.s3_session_store.clients") as clients_mock, \
         patch("src.agents.chat.s3_session_store.get_settings") as settings_mock:
        clients_mock.s3_sessions = fake_s3
        settings_mock.return_value.s3_sessions_bucket = "clyde-sessions"
        settings_mock.return_value.s3_sessions_prefix = "sessions"
        yield S3SessionStore(bucket="clyde-sessions", prefix="sessions")


def _key(session_id: str = "s1", subpath: str | None = None) -> SessionKey:
    base: SessionKey = {"project_key": "proj", "session_id": session_id}  # type: ignore[typeddict-item]
    if subpath:
        base["subpath"] = subpath
    return base


class TestS3SessionStore:
    async def test_load_missing_returns_none(
        self, store: S3SessionStore
    ) -> None:
        result = await store.load(_key("never-written"))
        assert result is None

    async def test_append_then_load_round_trip(
        self, store: S3SessionStore, fake_s3: _FakeS3
    ) -> None:
        key = _key("s1")
        entries = [
            {"type": "user", "uuid": "u1", "timestamp": "t1", "content": "hi"},
            {"type": "assistant", "uuid": "a1", "timestamp": "t2", "content": "hello"},
        ]
        await store.append(key, entries)
        loaded = await store.load(key)
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0]["uuid"] == "u1"
        assert loaded[1]["uuid"] == "a1"
        # Object key shape — prefix/project_key/session_id.jsonl
        assert "sessions/proj/s1.jsonl" in fake_s3.objects

    async def test_append_extends_existing(
        self, store: S3SessionStore
    ) -> None:
        key = _key("s2")
        await store.append(key, [{"type": "user", "uuid": "u1", "timestamp": "t1"}])
        await store.append(key, [{"type": "assistant", "uuid": "a1", "timestamp": "t2"}])
        loaded = await store.load(key)
        assert loaded is not None
        assert [e["uuid"] for e in loaded] == ["u1", "a1"]

    async def test_subpath_is_separate_object(
        self, store: S3SessionStore, fake_s3: _FakeS3
    ) -> None:
        main_key = _key("s3")
        sub_key = _key("s3", subpath="subagents/agent-x")
        await store.append(main_key, [{"type": "user", "uuid": "u1", "timestamp": "t1"}])
        await store.append(sub_key, [{"type": "assistant", "uuid": "a1", "timestamp": "t2"}])
        # Main + subagent stored under different keys.
        assert "sessions/proj/s3.jsonl" in fake_s3.objects
        assert "sessions/proj/s3/subagents/agent-x.jsonl" in fake_s3.objects

    async def test_delete_removes_main_and_subagents(
        self, store: S3SessionStore, fake_s3: _FakeS3
    ) -> None:
        main_key = _key("s4")
        sub_key = _key("s4", subpath="subagents/agent-x")
        await store.append(main_key, [{"type": "user", "uuid": "u1", "timestamp": "t1"}])
        await store.append(sub_key, [{"type": "assistant", "uuid": "a1", "timestamp": "t2"}])
        await store.delete(main_key)
        assert "sessions/proj/s4.jsonl" not in fake_s3.objects
        assert "sessions/proj/s4/subagents/agent-x.jsonl" not in fake_s3.objects

    async def test_empty_append_is_noop(
        self, store: S3SessionStore, fake_s3: _FakeS3
    ) -> None:
        await store.append(_key("s5"), [])
        # No object should have been created for an empty batch.
        assert all(not k.endswith("s5.jsonl") for k in fake_s3.objects)

    async def test_bucket_required(self) -> None:
        with patch("src.agents.chat.s3_session_store.get_settings") as s_mock:
            s_mock.return_value.s3_sessions_bucket = ""
            s_mock.return_value.s3_sessions_prefix = ""
            with pytest.raises(ValueError, match="bucket"):
                S3SessionStore()


class TestS3NotFoundMarker:
    """Make sure the internal marker doesn't bleed out as a generic
    ClientError to callers that try to ``except ClientError``."""

    def test_is_distinct_exception_type(self) -> None:
        assert not issubclass(_S3NotFound, ClientError)
