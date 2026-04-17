"""Database access for episodic and semantic memory tables.

All vector similarity searches use pgvector's cosine distance so that
results are ranked by semantic closeness regardless of vector magnitude.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import EPISODIC_RECALL_TOP_K, SEMANTIC_RECALL_TOP_K
from src.db.models.memory import CodeChunk, Episode


class MemoryRepository:
    """Encapsulates all SQL touching the episodes and code_chunks tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- episodic memory --

    async def save_episode(
        self,
        *,
        user_id: int,
        task_id: UUID,
        summary: str,
        outcome: str,
        metadata: dict | None = None,
        embedding: list[float],
    ) -> Episode:
        episode = Episode(
            user_id=user_id,
            task_id=task_id,
            summary=summary,
            outcome=outcome,
            metadata_=metadata or {},
            embedding=embedding,
        )
        self._session.add(episode)
        await self._session.flush()
        await self._session.refresh(episode)
        return episode

    async def recall_episodes(
        self,
        *,
        user_id: int,
        embedding: list[float],
        top_k: int = EPISODIC_RECALL_TOP_K,
    ) -> list[Episode]:
        """Find the most semantically similar past episodes for this user."""
        distance = Episode.embedding.cosine_distance(embedding)
        stmt = (
            select(Episode)
            .where(Episode.user_id == user_id)
            .order_by(distance)
            .limit(top_k)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # -- semantic (code) memory --

    async def save_code_chunks(self, *, chunks: list[dict]) -> list[CodeChunk]:
        """Bulk insert pre-built chunk dictionaries.

        Each dict is expected to contain all CodeChunk column values. We use
        individual ORM objects rather than `insert().values()` to benefit from
        server-side defaults (id, timestamps).
        """
        objects = [CodeChunk(**data) for data in chunks]
        self._session.add_all(objects)
        await self._session.flush()
        return objects

    async def recall_code_chunks(
        self,
        *,
        user_id: int,
        project_id: UUID,
        embedding: list[float],
        top_k: int = SEMANTIC_RECALL_TOP_K,
    ) -> list[CodeChunk]:
        """Find the most relevant code chunks for a project by vector similarity."""
        distance = CodeChunk.embedding.cosine_distance(embedding)
        stmt = (
            select(CodeChunk)
            .where(
                CodeChunk.user_id == user_id,
                CodeChunk.project_id == project_id,
            )
            .order_by(distance)
            .limit(top_k)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete_code_chunks_for_repo(self, *, repo_id: UUID) -> int:
        """Remove all chunks belonging to a repo, typically before re-indexing."""
        stmt = delete(CodeChunk).where(CodeChunk.repo_id == repo_id)
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[return-value]
