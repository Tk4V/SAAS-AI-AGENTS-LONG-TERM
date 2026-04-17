"""Semantic memory: indexes and searches project source code by meaning.

When the Tech Lead clones a repository it calls `index_repo` to chunk
every relevant source file and store the embeddings. Later, any agent
can call `search` with a natural-language query to retrieve the most
relevant code snippets without needing to grep through files manually.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import structlog

from src.config.constants import SEMANTIC_RECALL_TOP_K
from src.db.models.memory import CodeChunk
from src.db.queries.memory_queries import MemoryRepository
from src.memory.chunkers import CodeChunker
from src.memory.embeddings import EmbeddingClient

logger = structlog.get_logger("clyde.memory.semantic")

# How many chunk contents to embed in one API call. Keeps request sizes
# reasonable while still amortising the per-call overhead.
_EMBED_BATCH_SIZE = 20


class SemanticMemory:
    """High-level interface for indexing and querying project code."""

    def __init__(
        self,
        *,
        repository: MemoryRepository,
        embedder: EmbeddingClient,
        chunker: CodeChunker | None = None,
    ) -> None:
        self._repo = repository
        self._embedder = embedder
        self._chunker = chunker or CodeChunker()

    async def index_repo(
        self,
        *,
        user_id: int,
        project_id: UUID,
        repo_id: UUID,
        repo_path: Path,
        file_paths: list[str],
    ) -> int:
        """Chunk the given files, embed them, and store everything in the database.

        Any existing chunks for the repo are deleted first so that stale code
        does not pollute search results after a branch update.
        """
        deleted = await self._repo.delete_code_chunks_for_repo(repo_id=repo_id)
        if deleted:
            logger.info("semantic.cleared_stale_chunks", repo_id=str(repo_id), count=deleted)

        # Read and chunk every file. Skip files that cannot be read.
        all_chunks = []
        for rel_path in file_paths:
            full_path = repo_path / rel_path
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                logger.warning("semantic.file_read_error", path=str(full_path))
                continue

            chunks = self._chunker.chunk_file(content=content, file_path=rel_path)
            all_chunks.extend(chunks)

        if not all_chunks:
            logger.info("semantic.no_chunks", repo_id=str(repo_id))
            return 0

        # Embed in batches to avoid oversized API requests.
        contents = [c.content for c in all_chunks]
        embeddings: list[list[float]] = []
        for i in range(0, len(contents), _EMBED_BATCH_SIZE):
            batch = contents[i : i + _EMBED_BATCH_SIZE]
            batch_embeddings = await self._embedder.embed_texts(batch)
            embeddings.extend(batch_embeddings)

        # Build dicts matching CodeChunk columns and bulk-insert.
        rows = []
        for chunk_data, embedding in zip(all_chunks, embeddings):
            rows.append({
                "user_id": user_id,
                "project_id": project_id,
                "repo_id": repo_id,
                "file_path": chunk_data.path,
                "start_line": chunk_data.start_line,
                "end_line": chunk_data.end_line,
                "kind": chunk_data.kind,
                "symbol": chunk_data.symbol,
                "content": chunk_data.content,
                "embedding": embedding,
            })

        await self._repo.save_code_chunks(chunks=rows)
        logger.info(
            "semantic.indexed",
            repo_id=str(repo_id),
            files=len(file_paths),
            chunks=len(rows),
        )
        return len(rows)

    async def search(
        self,
        *,
        user_id: int,
        project_id: UUID,
        query: str,
        top_k: int = SEMANTIC_RECALL_TOP_K,
    ) -> list[CodeChunk]:
        """Find the code chunks most relevant to a natural-language query."""
        embedding = await self._embedder.embed_single(query)
        results = await self._repo.recall_code_chunks(
            user_id=user_id,
            project_id=project_id,
            embedding=embedding,
            top_k=top_k,
        )
        logger.debug(
            "semantic.searched",
            query_preview=query[:80],
            count=len(results),
        )
        return results
