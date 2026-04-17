"""Episodic memory: stores and recalls summaries of completed tasks.

After every task finishes, the Release Manager persists an episode that
captures what the task did and whether it succeeded. When a new task
arrives, the Tech Lead recalls similar episodes so the planning agents
can learn from past wins and avoid repeating past mistakes.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from src.config.constants import EPISODIC_RECALL_TOP_K
from src.db.models.memory import Episode
from src.db.queries.memory_queries import MemoryRepository
from src.memory.embeddings import EmbeddingClient

logger = structlog.get_logger("clyde.memory.episodic")


class EpisodicMemory:
    """High-level interface for storing and recalling task episodes."""

    def __init__(
        self,
        *,
        repository: MemoryRepository,
        embedder: EmbeddingClient,
    ) -> None:
        self._repo = repository
        self._embedder = embedder

    async def save(
        self,
        *,
        user_id: int,
        task_id: UUID,
        summary: str,
        outcome: str,
        metadata: dict | None = None,
    ) -> Episode:
        """Embed the summary and persist the episode."""
        embedding = await self._embedder.embed_single(summary)
        episode = await self._repo.save_episode(
            user_id=user_id,
            task_id=task_id,
            summary=summary,
            outcome=outcome,
            metadata=metadata,
            embedding=embedding,
        )
        logger.info(
            "episodic.saved",
            task_id=str(task_id),
            outcome=outcome,
        )
        return episode

    async def recall(
        self,
        *,
        user_id: int,
        query: str,
        top_k: int = EPISODIC_RECALL_TOP_K,
    ) -> list[Episode]:
        """Find past episodes most similar to the given query."""
        embedding = await self._embedder.embed_single(query)
        episodes = await self._repo.recall_episodes(
            user_id=user_id,
            embedding=embedding,
            top_k=top_k,
        )
        logger.debug(
            "episodic.recalled",
            query_preview=query[:80],
            count=len(episodes),
        )
        return episodes
