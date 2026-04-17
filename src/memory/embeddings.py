"""Voyage AI embedding client with in-memory caching.

Wraps the Voyage SDK to produce 1024-dimensional vectors for text. An LRU
cache keyed on the SHA-256 hash of each input avoids redundant API calls
when the same text (like a repeated task description) is embedded more than
once during a pipeline run.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from typing import Any

import structlog
import voyageai

from src.common.retry import RetryPolicy
from src.config import Settings, get_settings

logger = structlog.get_logger("clyde.memory.embeddings")

_CACHE_MAX_SIZE = 1000


class EmbeddingClient:
    """Produces vector embeddings via the Voyage AI API.

    The client is designed to be long-lived and shared across the process.
    It maintains an internal LRU cache so identical texts are never sent to
    the API twice.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._retry = retry_policy or RetryPolicy(
            max_attempts=3,
            base_delay=0.5,
            max_delay=10.0,
            name="voyage",
        )
        self._client: voyageai.Client = voyageai.Client(
            api_key=self._settings.voyage_api_key.get_secret_value(),
        )
        self._model = self._settings.voyage_model
        self._dimensions = self._settings.voyage_dimensions

        # Simple ordered-dict LRU: newest items at the end, oldest evicted first.
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    def _cache_key(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _put_cache(self, key: str, vector: list[float]) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return
        if len(self._cache) >= _CACHE_MAX_SIZE:
            self._cache.popitem(last=False)
        self._cache[key] = vector

    def _get_cache(self, key: str) -> list[float] | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    async def _call_api(self, texts: list[str]) -> list[list[float]]:
        """Call the Voyage embed endpoint in a thread since the SDK is synchronous."""

        def _sync_embed() -> Any:
            return self._client.embed(
                texts,
                model=self._model,
                output_dimension=self._dimensions,
            )

        result = await self._retry.run(asyncio.to_thread, _sync_embed)
        return result.embeddings  # type: ignore[no-any-return]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in a single batch call.

        Texts that already exist in the cache are served from memory; only
        uncached texts are sent to the API. The returned list is in the same
        order as the input.
        """
        if not texts:
            return []

        keys = [self._cache_key(t) for t in texts]
        results: list[list[float] | None] = [self._get_cache(k) for k in keys]

        # Collect texts that still need embedding.
        uncached_indices = [i for i, r in enumerate(results) if r is None]
        if uncached_indices:
            uncached_texts = [texts[i] for i in uncached_indices]
            logger.debug(
                "embedding.batch",
                total=len(texts),
                cache_hits=len(texts) - len(uncached_texts),
                api_calls=len(uncached_texts),
            )
            vectors = await self._call_api(uncached_texts)
            for idx, vec in zip(uncached_indices, vectors):
                self._put_cache(keys[idx], vec)
                results[idx] = vec

        return results  # type: ignore[return-value]

    async def embed_single(self, text: str) -> list[float]:
        """Convenience wrapper that embeds exactly one text."""
        return (await self.embed_texts([text]))[0]
