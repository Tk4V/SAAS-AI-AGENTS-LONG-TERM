"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-16 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgvector ships as a Postgres extension. RDS supports it on Postgres 15+
    # provided the parameter group enables `shared_preload_libraries=vector`.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    git_provider_kind = postgresql.ENUM(
        "github",
        name="git_provider_kind",
        create_type=False,
    )
    task_status = postgresql.ENUM(
        "running",
        "awaiting_ci",
        "fixing",
        "completed",
        "needs_human",
        "failed",
        name="task_status",
        create_type=False,
    )
    tool_kind = postgresql.ENUM(
        "mcp",
        "http",
        name="tool_kind",
        create_type=False,
    )
    git_provider_kind.create(op.get_bind(), checkfirst=True)
    task_status.create(op.get_bind(), checkfirst=True)
    tool_kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.UniqueConstraint("user_id", "name", name="uq_projects_user_id_name"),
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"])

    op.create_table(
        "project_repos",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", git_provider_kind, nullable=False, server_default="github"),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=False, server_default="main"),
        sa.Column("oauth_token_encrypted", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_project_repos"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            ondelete="CASCADE",
            name="fk_project_repos_project_id_projects",
        ),
        sa.UniqueConstraint("project_id", "url", name="uq_project_repos_project_id_url"),
    )
    op.create_index("ix_project_repos_project_id", "project_repos", ["project_id"])

    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("status", task_status, nullable=False, server_default="running"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("pr_urls", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            ondelete="CASCADE",
            name="fk_tasks_project_id_projects",
        ),
    )
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])

    op.create_table(
        "episodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_episodes"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"],
            ondelete="CASCADE",
            name="fk_episodes_task_id_tasks",
        ),
    )
    op.create_index("ix_episodes_user_id", "episodes", ["user_id"])
    op.create_index("ix_episodes_task_id", "episodes", ["task_id"])
    # HNSW index for cosine similarity. Voyage embeddings are normalised, so
    # cosine and inner product give the same ranking but cosine is the safer
    # default if we ever store unnormalised vectors next to them.
    op.execute(
        "CREATE INDEX ix_episodes_embedding_hnsw ON episodes "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "code_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="function"),
        sa.Column("symbol", sa.String(length=500), nullable=True),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_code_chunks"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            ondelete="CASCADE",
            name="fk_code_chunks_project_id_projects",
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["project_repos.id"],
            ondelete="CASCADE",
            name="fk_code_chunks_repo_id_project_repos",
        ),
    )
    op.create_index("ix_code_chunks_user_id", "code_chunks", ["user_id"])
    op.create_index("ix_code_chunks_project_id", "code_chunks", ["project_id"])
    op.create_index("ix_code_chunks_repo_id", "code_chunks", ["repo_id"])
    op.execute(
        "CREATE INDEX ix_code_chunks_embedding_hnsw ON code_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("model_alias", sa.String(length=64), nullable=False, server_default="sonnet"),
        sa.Column("system_prompt", sa.String(), nullable=False, server_default=""),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agents"),
        sa.UniqueConstraint("user_id", "slug", name="uq_agents_user_id_slug"),
    )
    op.create_index("ix_agents_user_id", "agents", ["user_id"])

    op.create_table(
        "tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("kind", tool_kind, nullable=False, server_default="mcp"),
        sa.Column("endpoint", sa.String(length=500), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("credentials_encrypted", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tools"),
        sa.UniqueConstraint("user_id", "slug", name="uq_tools_user_id_slug"),
    )
    op.create_index("ix_tools_user_id", "tools", ["user_id"])

    op.create_table(
        "pipelines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("graph", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pipelines"),
        sa.UniqueConstraint("user_id", "slug", name="uq_pipelines_user_id_slug"),
    )
    op.create_index("ix_pipelines_user_id", "pipelines", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_pipelines_user_id", table_name="pipelines")
    op.drop_table("pipelines")

    op.drop_index("ix_tools_user_id", table_name="tools")
    op.drop_table("tools")

    op.drop_index("ix_agents_user_id", table_name="agents")
    op.drop_table("agents")

    op.execute("DROP INDEX IF EXISTS ix_code_chunks_embedding_hnsw")
    op.drop_index("ix_code_chunks_repo_id", table_name="code_chunks")
    op.drop_index("ix_code_chunks_project_id", table_name="code_chunks")
    op.drop_index("ix_code_chunks_user_id", table_name="code_chunks")
    op.drop_table("code_chunks")

    op.execute("DROP INDEX IF EXISTS ix_episodes_embedding_hnsw")
    op.drop_index("ix_episodes_task_id", table_name="episodes")
    op.drop_index("ix_episodes_user_id", table_name="episodes")
    op.drop_table("episodes")

    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    op.drop_index("ix_tasks_user_id", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ix_project_repos_project_id", table_name="project_repos")
    op.drop_table("project_repos")

    op.drop_index("ix_projects_user_id", table_name="projects")
    op.drop_table("projects")

    op.execute("DROP TYPE IF EXISTS tool_kind")
    op.execute("DROP TYPE IF EXISTS task_status")
    op.execute("DROP TYPE IF EXISTS git_provider_kind")
