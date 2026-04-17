"""Unified memory facade used by agents to retrieve context.

The MemoryManager composes episodic and semantic memory behind a single
`get_context` call that agents use at the start of every planning step.
Both recall paths run in parallel so latency is the max of the two rather
than the sum.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import structlog

from src.db.models.memory import CodeChunk, Episode
from src.memory.episodic import EpisodicMemory
from src.memory.semantic import SemanticMemory

logger = structlog.get_logger("clyde.memory.manager")


@dataclass
class MemoryContext:
    """The combined recall result that agents consume."""

    episodes: list[Episode] = field(default_factory=list)
    code_chunks: list[CodeChunk] = field(default_factory=list)


class MemoryManager:
    """Single entry point for all memory operations.

    Agents should not import EpisodicMemory or SemanticMemory directly;
    this class exposes the right methods and handles cross-cutting concerns
    like parallel recall.
    """

    def __init__(
        self,
        *,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
    ) -> None:
        self._episodic = episodic
        self._semantic = semantic

    async def get_context(
        self,
        *,
        user_id: int,
        project_id: UUID,
        query: str,
    ) -> MemoryContext:
        """Retrieve both episodic and semantic memories in parallel."""
        episodes_coro = self._episodic.recall(user_id=user_id, query=query)
        code_coro = self._semantic.search(
            user_id=user_id,
            project_id=project_id,
            query=query,
        )

        episodes, code_chunks = await asyncio.gather(
            episodes_coro,
            code_coro,
            return_exceptions=False,
        )

        logger.debug(
            "memory.context_loaded",
            episodes=len(episodes),
            code_chunks=len(code_chunks),
        )
        return MemoryContext(episodes=episodes, code_chunks=code_chunks)

    async def save_episode(
        self,
        *,
        user_id: int,
        task_id: UUID,
        summary: str,
        outcome: str,
        metadata: dict | None = None,
    ) -> Episode:
        """Persist a completed-task episode for future recall."""
        return await self._episodic.save(
            user_id=user_id,
            task_id=task_id,
            summary=summary,
            outcome=outcome,
            metadata=metadata,
        )

    async def index_repo(
        self,
        *,
        user_id: int,
        project_id: UUID,
        repo_id: UUID,
        repo_path: Path,
        file_paths: list[str],
    ) -> int:
        """Chunk, embed, and store a repository's source files."""
        return await self._semantic.index_repo(
            user_id=user_id,
            project_id=project_id,
            repo_id=repo_id,
            repo_path=repo_path,
            file_paths=file_paths,
        )

    async def search_code(
        self,
        *,
        user_id: int,
        project_id: UUID,
        query: str,
        top_k: int | None = None,
    ) -> list[CodeChunk]:
        """Search indexed code by natural-language query."""
        kwargs: dict = {
            "user_id": user_id,
            "project_id": project_id,
            "query": query,
        }
        if top_k is not None:
            kwargs["top_k"] = top_k
        return await self._semantic.search(**kwargs)
