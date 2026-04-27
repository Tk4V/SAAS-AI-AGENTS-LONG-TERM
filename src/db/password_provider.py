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
            if self._cached is None or (time.monotonic() - self._fetched_at) >= self._ttl:
                self._cached = await asyncio.to_thread(self._fetch)
                self._fetched_at = time.monotonic()
        return self._cached

    def _fetch(self) -> str:
        """Blocking boto3 call — always run via ``asyncio.to_thread``."""
        import boto3  # imported here to keep startup fast when SM is not configured

        client = boto3.client("secretsmanager", region_name=self._region)
        response = client.get_secret_value(SecretId=self._secret_name)
        return str(json.loads(response["SecretString"])["password"])
