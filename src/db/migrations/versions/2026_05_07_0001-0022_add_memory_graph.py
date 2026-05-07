"""Add memory_nodes and memory_edges tables for agent cross-task memory graph.

Revision ID: 0022_add_memory_graph
Revises: 0021_user_agents
Create Date: 2026-05-07 00:00:00.000000

Introduces the property-graph tables that back Option B (Memory MCP Server)
from docs/agent-memory-graph.md.

Tables created
--------------
* ``memory_nodes`` — universal node table storing task, action, and entity
  nodes. Uses a BigInteger identity PK (not UUID) for high insert volume.
  Carries a nullable ``embedding`` column (task nodes only) and a
  ``search_text`` generated TSVECTOR column for full-text search over the
  JSONB ``properties`` field. The generated column is added via raw SQL
  because SQLAlchemy's ``op.create_table`` does not support
  ``GENERATED ALWAYS AS ... STORED``.

* ``memory_edges`` — typed, weighted edges between nodes. Enforces
  ``UNIQUE(source_id, target_id, edge_type)`` so duplicate edges are
  rejected at the DB level; weight is incremented via UPDATE instead.

Indexes
-------
* ``idx_memory_nodes_type``           — node_type filter
* ``idx_memory_nodes_props``          — GIN on properties JSONB
* ``idx_memory_nodes_search_text``    — GIN on search_text for tsvector queries
* ``idx_memory_nodes_embedding_hnsw`` — HNSW for cosine similarity (pgvector)
* ``idx_memory_edges_source``         — (source_id, edge_type) outbound traversal
* ``idx_memory_edges_target``         — (target_id, edge_type) inbound traversal
* ``idx_memory_edges_type``           — edge_type filter
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0022_add_memory_graph"
down_revision: Union[str, None] = "0021_user_agents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── memory_nodes ─────────────────────────────────────────────────────────
    op.create_table(
        "memory_nodes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("node_type", sa.String(50), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memory_nodes"),
    )

    # Generated column — must be added via raw SQL (SQLAlchemy does not support
    # GENERATED ALWAYS AS ... STORED in op.create_table).
    op.execute(sa.text("""
        ALTER TABLE memory_nodes
        ADD COLUMN search_text TSVECTOR
        GENERATED ALWAYS AS (
            to_tsvector('english',
                coalesce(properties->>'description', '') || ' ' ||
                coalesce(properties->>'tool_name',   '') || ' ' ||
                coalesce(properties->>'identifier',  ''))
        ) STORED
    """))

    op.create_index("idx_memory_nodes_type", "memory_nodes", ["node_type"])
    op.create_index(
        "idx_memory_nodes_props",
        "memory_nodes",
        ["properties"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_memory_nodes_search_text",
        "memory_nodes",
        ["search_text"],
        postgresql_using="gin",
    )
    # HNSW index for cosine similarity — pgvector index type, requires raw SQL.
    op.execute(
        "CREATE INDEX idx_memory_nodes_embedding_hnsw "
        "ON memory_nodes USING hnsw (embedding vector_cosine_ops)"
        " WHERE embedding IS NOT NULL"
    )

    # ── memory_edges ─────────────────────────────────────────────────────────
    op.create_table(
        "memory_edges",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("edge_type", sa.String(50), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column(
            "properties",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memory_edges"),
        sa.UniqueConstraint(
            "source_id",
            "target_id",
            "edge_type",
            name="uq_memory_edges_source_target_type",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["memory_nodes.id"],
            ondelete="CASCADE",
            name="fk_memory_edges_source_id_memory_nodes",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["memory_nodes.id"],
            ondelete="CASCADE",
            name="fk_memory_edges_target_id_memory_nodes",
        ),
    )

    op.create_index(
        "idx_memory_edges_source",
        "memory_edges",
        ["source_id", "edge_type"],
    )
    op.create_index(
        "idx_memory_edges_target",
        "memory_edges",
        ["target_id", "edge_type"],
    )
    op.create_index("idx_memory_edges_type", "memory_edges", ["edge_type"])


def downgrade() -> None:
    op.drop_index("idx_memory_edges_type", table_name="memory_edges")
    op.drop_index("idx_memory_edges_target", table_name="memory_edges")
    op.drop_index("idx_memory_edges_source", table_name="memory_edges")
    op.drop_table("memory_edges")

    op.execute("DROP INDEX IF EXISTS idx_memory_nodes_embedding_hnsw")
    op.drop_index("idx_memory_nodes_search_text", table_name="memory_nodes")
    op.drop_index("idx_memory_nodes_props", table_name="memory_nodes")
    op.drop_index("idx_memory_nodes_type", table_name="memory_nodes")
    op.drop_table("memory_nodes")
