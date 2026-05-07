"""ORM models for the agent memory property graph.


Design notes
------------
* BigInteger identity PK (not UUID) — these tables have high insert volume;
  sequential integers are faster to index and join than UUIDs.
* ``search_text`` is intentionally omitted from the ORM — it is a
  GENERATED ALWAYS AS STORED column maintained entirely by Postgres. Reading
  it back from Python is not needed; the GraphWriter never writes to it.
* ``MemoryEdge`` has no ``updated_at`` — edges are immutable after creation.
  Weight increments are applied via a targeted UPDATE in GraphWriter, not
  via ORM attribute mutation.
* ``user_id`` and other node-type-specific fields live inside ``properties``
  JSONB rather than dedicated columns, keeping the schema universal across
  all node types.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Identity, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pgvector.sqlalchemy import Vector

from src.db.base import Base, TimestampMixin


class MemoryNode(Base, TimestampMixin):
    """Universal node — stores task, action, and entity nodes.

    ``node_type`` discriminates the shape of ``properties``:

    task   — {"task_id", "user_id", "agent_id", "description", "status", "attempt"}
    action — {"tool_name", "tool_use_id", "turn", "detail", "outcome", "is_error"}
    entity — {"kind", "identifier"}  e.g. kind="file", identifier="src/payments/service.py"
    """

    __tablename__ = "memory_nodes"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=False),
        primary_key=True,
    )
    node_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    properties: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    # Populated for 'task' nodes only; NULL for action and entity nodes.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    # search_text (TSVECTOR generated column) is intentionally omitted —
    # Postgres maintains it automatically; we never write to it from Python.


class MemoryEdge(Base):
    """Typed, weighted directed edge between two memory nodes.

    The UNIQUE constraint on (source_id, target_id, edge_type) is enforced at
    the DB level. GraphWriter uses ON CONFLICT DO UPDATE to increment weight
    instead of inserting a duplicate edge.

    Edge types in use:
        executed  — task → action
        read      — action → entity:file
        wrote     — action → entity:file
        called    — action → entity:api or entity:subagent
        targeted  — action → entity:repo
    """

    __tablename__ = "memory_edges"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=False),
        primary_key=True,
    )
    source_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("memory_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("memory_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    edge_type: Mapped[str] = mapped_column(String(50), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    properties: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
