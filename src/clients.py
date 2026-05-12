"""Process-wide network clients with connection pools.

Everything here owns something that must be closed on shutdown — an
`httpx.AsyncClient` pool, an Anthropic SDK session, an outbound socket. The
shared rule of thumb: if a member needs `await something.close()` or
`aclose()`, it lives here. Pure config-derived utilities go to
`src/app_context.py`.

Members:
- `anthropic` — shared `AsyncAnthropic` SDK client. Every agent that calls
  Claude reuses this so the underlying httpx connection pool is amortised.
- `http`      — shared `httpx.AsyncClient`. Passed into every per-request
  API client (e.g. `GitHubApiClient`) so the connection pool to provider
  hosts is reused across all callers in the process.

`dispose()` must be called on application shutdown, typically from
`Application._lifespan`. Forgetting it leaks open sockets.
"""

from __future__ import annotations

from typing import Any

import boto3
import httpx
import redis.asyncio as redis
import structlog
from anthropic import AsyncAnthropic

from src.config import Settings, get_settings


class Clients:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._http: httpx.AsyncClient | None = None
        self._anthropic: AsyncAnthropic | None = None
        self._redis: redis.Redis | None = None
        self._s3_sessions: Any | None = None
        self._logger = structlog.get_logger("clyde.clients")

    @property
    def http(self) -> httpx.AsyncClient:
        """Shared httpx client. Used by every `BaseApiClient` subclass so
        provider HTTP connection pools are reused across the process."""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    @property
    def anthropic(self) -> AsyncAnthropic:
        """Shared Anthropic SDK client. Reused across agents to amortise the
        underlying httpx connection pool."""
        if self._anthropic is None:
            self._anthropic = AsyncAnthropic(
                api_key=self._settings.anthropic_api_key.get_secret_value(),
            )
        return self._anthropic

    @property
    def s3_sessions(self) -> Any:
        """Shared boto3 S3 client used by the chat-session transcript store.

        Synchronous client — call sites wrap in ``asyncio.to_thread`` since
        each turn produces only a handful of GET/PUT operations and we
        don't want an ``aioboto3`` dependency just for that. The boto3 S3
        client is documented to be thread-safe for concurrent use.

        Settings must populate ``s3_sessions_bucket`` for any chat code to
        actually use this; an unset bucket means "store sessions only on
        local disk", which is fine for dev but means resume across
        container restarts will silently not work.
        """
        if self._s3_sessions is None:
            region = (
                self._settings.s3_sessions_region or self._settings.aws_region or None
            )
            self._s3_sessions = boto3.client(
                "s3",
                region_name=region,
            )
        return self._s3_sessions

    @property
    def redis(self) -> redis.Redis:
        """Shared Redis client used by EventBroadcaster and permission_gate.

        Lazily connects on first access. The connection pool is reused across
        publish/subscribe sites — pubsub() returns a separate dedicated
        connection per subscriber, which is fine and what we want.
        """
        if self._redis is None:
            self._redis = redis.from_url(
                self._settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    async def dispose(self) -> None:
        """Close every owned client. Call from the application lifespan exit."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        if self._anthropic is not None:
            # Anthropic SDK exposes close() as a coroutine on the async client.
            await self._anthropic.close()
            self._anthropic = None
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        # boto3 S3 client doesn't expose a close() — drop the reference so
        # GC can collect it; the underlying urllib3 pools clean themselves.
        self._s3_sessions = None
        self._logger.info("clients.disposed")


clients = Clients()
