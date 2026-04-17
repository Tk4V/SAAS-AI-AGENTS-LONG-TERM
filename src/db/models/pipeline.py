"""PipelineRecord ORM model.

Milestone 2 will let users define their own pipelines (sequences of agents
with custom routers). For M1 the default development pipeline is hard-coded in
`src.engine.graph_builder`; this table sits ready for the moment users start
shipping their own.
"""

from __future__ import annotations

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, UserScopeMixin


class PipelineRecord(Base, UUIDPrimaryKeyMixin, UserScopeMixin, TimestampMixin):
    __tablename__ = "pipelines"
    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_pipelines_user_id_slug"),
    )

    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Declarative graph definition: nodes (agent slugs) and edges. Validated by
    # the engine before being compiled into a LangGraph StateGraph.
    graph: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
