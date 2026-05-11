"""TaskMessage ORM model — append-only chat history for a task.

Every message between the user and the agent team flows through this table:
free-form chat from either side, agent approval requests, the user's
responses to them, status transitions and pipeline errors. The frontend
bootstraps the chat panel from `GET /tasks/{id}/messages` and then receives
new rows in real time over the bidirectional WebSocket.
"""

from __future__ import annotations

import enum
from typing import Any
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, UserScopeMixin


class MessageRole(str, enum.Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class MessageKind(str, enum.Enum):
    CHAT = "chat"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_RESPONSE = "approval_response"
    STATUS = "status"
    ERROR = "error"


class TaskMessage(Base, UUIDPrimaryKeyMixin, UserScopeMixin, TimestampMixin):
    __tablename__ = "task_messages"

    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    kind: Mapped[MessageKind] = mapped_column(
        Enum(MessageKind, name="message_kind", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=MessageKind.CHAT,
        index=True,
    )
    # Free-form text body. For approval_request/response the human-readable
    # summary lives here; the structured payload (approval_id, tool name,
    # user-supplied JSON, etc.) goes into `meta`.
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Author label — agent name for role=agent, "system" for role=system,
    # null for role=user. Stored separately from role so the UI can show
    # "TechLead asked..." vs "Publisher asked..." without parsing meta.
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
