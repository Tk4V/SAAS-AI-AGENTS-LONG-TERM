"""TaskApproval ORM model.

Represents a single tool-use permission request raised by the orchestrator's
`can_use_tool` callback. The pipeline pauses until the owning user resolves
the approval via the HTTP API.
"""

from __future__ import annotations

import enum
from typing import Any
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, UserScopeMixin


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class TaskApproval(Base, UUIDPrimaryKeyMixin, UserScopeMixin, TimestampMixin):
    __tablename__ = "task_approvals"

    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    tool_input: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_status", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=ApprovalStatus.PENDING,
        index=True,
    )
    # Free-form payload that the user attached to the approval response.
    # Shape is up to the caller: a plain text answer, structured JSON for
    # an `ask_user` tool, or null when the user just clicked approve/deny.
    user_response: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, default=None
    )
