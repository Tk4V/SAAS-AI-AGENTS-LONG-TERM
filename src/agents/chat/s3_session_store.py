"""S3-backed implementation of the Claude Agent SDK ``SessionStore`` protocol.

The SDK mirrors transcript entries to whatever adapter it's given in
``ClaudeAgentOptions.session_store``. We point that at S3 so the JSONL
bytes survive a container destroy: any future pod with the same
``session_id`` and credentials can ``load()`` the full conversation and
resume right where it left off.

Layout in the bucket
--------------------
``{prefix}/{project_key}/{session_id}.jsonl`` for the main transcript.
``{prefix}/{project_key}/{session_id}/{subpath}.jsonl`` for subagent
transcripts (the SDK includes ``subpath`` in ``SessionKey`` for those).

Why JSONL: matches the SDK's on-disk format byte-for-byte, makes ad-hoc
inspection (``aws s3 cp s3://... -`` + ``jq``) trivial, and lets us
``append`` cheaply by reading-extending-rewriting one object. With our
session sizes (typically <1 MB per transcript) full-object reads are
fast enough; if a session ever grows past tens of MB we'd switch to
multipart append, but that hasn't happened in practice.

Only ``append`` / ``load`` / ``delete`` are implemented — the SDK probes
for ``list_sessions`` / ``list_session_summaries`` at runtime and falls
back gracefully when they're absent. We don't need them for resume.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import structlog
from botocore.exceptions import ClientError
from claude_agent_sdk.types import (
    SessionKey,
    SessionStore,
    SessionStoreEntry,
)

from src.clients import clients
from src.config import get_settings

_logger = structlog.get_logger("clyde.agents.chat.s3_session_store")


class S3SessionStore(SessionStore):
    """Mirror SDK transcripts to S3 so any pod can resume any session.

    Single instance is reused across every chat session in the process —
    boto3's S3 client is thread-safe, and the SDK calls ``append``/``load``
    serially per session, so there's no contention to worry about beyond
    eventual consistency on cross-session writes (which never collide on
    keys).
    """

    def __init__(
        self,
        *,
        bucket: str | None = None,
        prefix: str | None = None,
    ) -> None:
        settings = get_settings()
        self._bucket = bucket or settings.s3_sessions_bucket
        self._prefix = (prefix or settings.s3_sessions_prefix or "").strip("/")
        if not self._bucket:
            raise ValueError(
                "S3SessionStore initialised without a bucket. "
                "Set S3_SESSIONS_BUCKET in settings or pass bucket= explicitly."
            )

    # ── public protocol methods ───────────────────────────────────────────

    async def append(
        self, key: SessionKey, entries: list[SessionStoreEntry]
    ) -> None:
        """Read the current JSONL blob, append entries, write it back.

        Concurrency note: the SDK serialises ``append`` calls per
        ``ClaudeSDKClient`` instance, so within a single chat session
        there's no race. Across pods running different sessions, keys are
        disjoint (sessions are tied to tasks, and only one pod owns a
        task's session at a time per ``chat_session_service`` registry).
        """
        if not entries:
            return
        object_key = self._object_key(key)
        try:
            existing = await self._read_jsonl(object_key)
        except _S3NotFound:
            existing = []
        existing.extend(entries)
        await self._write_jsonl(object_key, existing)

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        """Return all entries for a session, or ``None`` if the object
        doesn't exist (treat as "never written")."""
        object_key = self._object_key(key)
        try:
            entries = await self._read_jsonl(object_key)
        except _S3NotFound:
            return None
        return entries or None

    async def delete(self, key: SessionKey) -> None:
        """Remove the main transcript object plus any subagent subobjects.

        Called when the task is completed/failed so the bucket doesn't
        accumulate dead transcripts. S3 lifecycle rules can be a backup
        for any we miss.
        """
        object_key = self._object_key(key)
        await asyncio.to_thread(
            self._delete_one, object_key,
        )
        # If we're deleting the main transcript, also drop the per-session
        # "directory" prefix that holds subagent subobjects. We don't track
        # them individually — a single list-and-delete sweep is fine.
        if key.get("subpath") is None:
            await asyncio.to_thread(
                self._delete_prefix,
                self._session_prefix(key),
            )

    # ── helpers ────────────────────────────────────────────────────────────

    def _object_key(self, key: SessionKey) -> str:
        project = key["project_key"]
        session = key["session_id"]
        subpath = key.get("subpath")
        head = f"{self._prefix}/{project}/{session}" if self._prefix else f"{project}/{session}"
        if subpath:
            return f"{head}/{subpath}.jsonl"
        return f"{head}.jsonl"

    def _session_prefix(self, key: SessionKey) -> str:
        project = key["project_key"]
        session = key["session_id"]
        head = f"{self._prefix}/{project}/{session}" if self._prefix else f"{project}/{session}"
        return f"{head}/"

    async def _read_jsonl(self, object_key: str) -> list[SessionStoreEntry]:
        """Fetch the object, parse line-by-line, return as a list. Raises
        ``_S3NotFound`` when the object is absent so callers can branch."""
        body = await asyncio.to_thread(self._get_object_body, object_key)
        entries: list[SessionStoreEntry] = []
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(cast(SessionStoreEntry, json.loads(line)))
            except json.JSONDecodeError:
                _logger.warning(
                    "s3_session_store.bad_line",
                    object_key=object_key,
                    preview=line[:200],
                )
        return entries

    async def _write_jsonl(
        self, object_key: str, entries: list[SessionStoreEntry]
    ) -> None:
        body = "\n".join(json.dumps(e, separators=(",", ":")) for e in entries)
        if body and not body.endswith("\n"):
            body += "\n"
        await asyncio.to_thread(self._put_object, object_key, body.encode("utf-8"))

    # ── sync boto3 wrappers (called via to_thread) ────────────────────────

    def _get_object_body(self, object_key: str) -> str:
        try:
            response: dict[str, Any] = clients.s3_sessions.get_object(
                Bucket=self._bucket, Key=object_key,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404"):
                raise _S3NotFound(object_key) from None
            raise
        return response["Body"].read().decode("utf-8")

    def _put_object(self, object_key: str, body: bytes) -> None:
        clients.s3_sessions.put_object(
            Bucket=self._bucket,
            Key=object_key,
            Body=body,
            ContentType="application/x-ndjson",
        )

    def _delete_one(self, object_key: str) -> None:
        clients.s3_sessions.delete_object(Bucket=self._bucket, Key=object_key)

    def _delete_prefix(self, prefix: str) -> None:
        """Best-effort sweep of all objects under a prefix. Used on session
        delete to clean up subagent subobjects we don't track individually."""
        paginator = clients.s3_sessions.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            contents = page.get("Contents") or []
            if not contents:
                continue
            clients.s3_sessions.delete_objects(
                Bucket=self._bucket,
                Delete={
                    "Objects": [{"Key": obj["Key"]} for obj in contents],
                    "Quiet": True,
                },
            )


class _S3NotFound(Exception):
    """Internal marker for ``GetObject`` 404 — keeps the public API
    surface narrow so callers can't accidentally catch it as a generic
    ``ClientError``."""
