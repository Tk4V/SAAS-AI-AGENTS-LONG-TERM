"""Project and ProjectRepo ORM models.

A user owns one or more projects. Each project groups one or more git
repositories. Tasks always live under a project, so the project is the unit at
which we plan and apply cross-repo changes.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, UserScopeMixin

if TYPE_CHECKING:
    from src.db.models.task import Task


class GitProviderKind(str, enum.Enum):
    """Supported git providers. M1 ships GitHub only; the enum exists so we can
    add GitLab or Bitbucket without a column type change later.
    """

    GITHUB = "github"


class Project(Base, UUIDPrimaryKeyMixin, UserScopeMixin, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_projects_user_id_name"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    repos: Mapped[list["ProjectRepo"]] = relationship(
        "ProjectRepo",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    tasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ProjectRepo(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A single git repository attached to a project.

    OAuth tokens are stored per-user in `user_oauth_credentials`, not per-repo.
    The `oauth_token_encrypted` column exists in the schema for a future
    per-repo override scenario but is not populated in M1.
    """

    __tablename__ = "project_repos"
    __table_args__ = (
        UniqueConstraint("project_id", "url", name="uq_project_repos_project_id_url"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[GitProviderKind] = mapped_column(
        Enum(GitProviderKind, name="git_provider_kind", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=GitProviderKind.GITHUB,
    )
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    oauth_token_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="repos")
