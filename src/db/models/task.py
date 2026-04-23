"""Task ORM model.

A task is the unit of work the agent team operates on. Its lifecycle is tracked
by the `status` field; the `state` JSONB carries the LangGraph state snapshot
between agent steps and after webhook-driven retries.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, UserScopeMixin

if TYPE_CHECKING:
    from src.db.models.project import Project


class TaskStatus(str, enum.Enum):
    """Lifecycle of a task. Persisted as a Postgres enum, so adding a new value
    requires an Alembic migration that calls ALTER TYPE ... ADD VALUE.
    """

    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_CI = "awaiting_ci"
    FIXING = "fixing"
    COMPLETED = "completed"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class Task(Base, UUIDPrimaryKeyMixin, UserScopeMixin, TimestampMixin):
    __tablename__ = "tasks"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=TaskStatus.RUNNING,
        index=True,
    )

    # Number of fix attempts already consumed by the webhook CI handler. Capped by
    # settings.max_fix_attempts before transitioning to NEEDS_HUMAN.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Full LangGraph state snapshot. Used to resume the pipeline after a CI
    # webhook arrives or after a process restart.
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # URLs of pull requests created by Publisher agent, keyed by repo full name.
    # Shape: {"owner/repo": "https://github.com/owner/repo/pull/123"}.
    pr_urls: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)

    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="tasks")
