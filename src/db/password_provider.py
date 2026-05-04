"""AWS Secrets Manager password provider for asyncpg connection pooling.

asyncpg accepts a ``password`` argument that can be an async callable.
The pool calls it for every new physical connection, so rotating the secret
in AWS Secrets Manager is enough — the app picks it up automatically within
one TTL window without any restart or env-file change.
"""

from __future__ import annotations

import asyncio
import json
import time

import structlog

logger = structlog.get_logger("clyde.db.password_provider")


class SecretsManagerPasswordProvider:
    """Async callable that supplies a DB password fetched from AWS Secrets Manager.

    A TTL-based in-process cache prevents a Secrets Manager call on every
    individual connection.  The default TTL matches ``db_pool_recycle_sec``
    (1800 s) so that by the time a pooled connection is recycled the provider
    will re-fetch if the secret has been rotated.

    Usage::

        provider = SecretsManagerPasswordProvider("prod/clyde/db", "us-east-1")
        engine = create_async_engine(url, connect_args={"password": provider})
    """

    def __init__(
        self,
        secret_name: str,
        region: str,
        ttl_seconds: int = 1800,
    ) -> None:
        self._secret_name = secret_name
        self._region = region
        self._ttl = ttl_seconds
        self._cached: str | None = None
        self._fetched_at: float = 0.0
        self._lock: asyncio.Lock | None = None  # created lazily inside the running loop

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def __call__(self) -> str:
        """Return the current DB password, fetching from Secrets Manager if stale."""
        async with self._get_lock():
            if self._cached is not None and (time.monotonic() - self._fetched_at) < self._ttl:
                logger.debug("password cache hit", secret_name=self._secret_name)
                return self._cached
            logger.info(
                "fetching password from SM",
                secret_name=self._secret_name,
                region=self._region,
            )
            start = time.monotonic()
            self._cached = await asyncio.to_thread(self._fetch)
            self._fetched_at = time.monotonic()
            logger.info(
                "fetched password from SM",
                secret_name=self._secret_name,
                latency_ms=round((self._fetched_at - start) * 1000, 1),
            )
            return self._cached

    async def invalidate(self) -> None:
        """Drop the cached password so the next call re-fetches from SM.

        Called from the DB session layer after a Postgres auth failure so a
        rotated secret is picked up without waiting for the TTL to expire.
        """
        async with self._get_lock():
            self._cached = None
            self._fetched_at = 0.0
            logger.warning("password cache invalidated", secret_name=self._secret_name)

    def _fetch(self) -> str:
        """Blocking boto3 call — always run via ``asyncio.to_thread``."""
        import boto3  # imported here to keep startup fast when SM is not configured
        from botocore.exceptions import ClientError

        try:
            client = boto3.client("secretsmanager", region_name=self._region)
            response = client.get_secret_value(SecretId=self._secret_name)
            return str(json.loads(response["SecretString"])["password"])
        except (ClientError, KeyError, json.JSONDecodeError) as exc:
            logger.error(
                "failed to fetch password from SM",
                secret_name=self._secret_name,
                region=self._region,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise
