"""Memory ORM models: episodic past tasks and semantic code chunks.

Both tables store a `voyage-3-large` embedding in a 1024-dimensional pgvector
column. HNSW indexes on those columns are created in the Alembic migration so
that vector search stays sub-second even on large corpora.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.config import get_settings
from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, UserScopeMixin

if TYPE_CHECKING:
    from src.db.models.project import Project, ProjectRepo
    from src.db.models.task import Task


# Read once so models can use it as a column dimension. Voyage's voyage-3-large
# returns 1024 floats; if you switch models later, update VOYAGE_DIMENSIONS in
# .env and write a migration that re-creates the columns at the new size.
_EMBEDDING_DIM = get_settings().voyage_dimensions


class ChunkKind(str, enum.Enum):
    """Granularity at which a code file was split for indexing."""

    FUNCTION = "function"
    CLASS = "class"
    MODULE = "module"
    BLOCK = "block"


class Episode(Base, UUIDPrimaryKeyMixin, UserScopeMixin, TimestampMixin):
    """A summary of a completed task with its outcome.

    Used by future tasks to recall past solutions: when a new task arrives we
    embed its description and find the most similar episodes so the planning
    agents can reuse what worked before.
    """

    __tablename__ = "episodes"

    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    summary: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    embedding: Mapped[list[float]] = mapped_column(Vector(_EMBEDDING_DIM), nullable=False)

    task: Mapped["Task"] = relationship("Task", lazy="joined")


class CodeChunk(Base, UUIDPrimaryKeyMixin, UserScopeMixin, TimestampMixin):
    """An indexed slice of source code from one of a project's repositories.

    Tech Lead and Senior Developer query this table to ground their LLM calls
    in actual project code rather than relying on the model's prior knowledge.
    """

    __tablename__ = "code_chunks"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    repo_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("project_repos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[ChunkKind] = mapped_column(
        String(32),
        nullable=False,
        default=ChunkKind.FUNCTION.value,
    )
    symbol: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(String, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(_EMBEDDING_DIM), nullable=False)

    project: Mapped["Project"] = relationship("Project", lazy="joined")
    repo: Mapped["ProjectRepo"] = relationship("ProjectRepo", lazy="joined")
