"""Pydantic schemas for the task chat: REST history + WS envelopes."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.db.models.task_message import MessageKind, MessageRole

if TYPE_CHECKING:
    from src.db.models.task_message import TaskMessage


class TaskMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    role: MessageRole
    kind: MessageKind
    content: str
    author: str | None
    meta: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_orm(cls, message: "TaskMessage") -> "TaskMessageRead":
        return cls(
            id=message.id,
            task_id=message.task_id,
            role=message.role,
            kind=message.kind,
            content=message.content,
            author=message.author,
            meta=message.meta or {},
            created_at=message.created_at,
        )


class WSApprovalResponse(BaseModel):
    """Inbound: user resolves a pending approval, optionally with a payload.

    ``payload`` is opaque to the gate — for free-text answers send
    ``{"text": "..."}``; for ``ask_user`` tool responses send the structured
    answer the agent expects.
    """

    type: Literal["approval_response"]
    approval_id: UUID
    approved: bool
    payload: dict[str, Any] = Field(default_factory=dict)


class WSChatMessage(BaseModel):
    """Inbound: free-form chat from the user, not tied to any approval.

    Routed into the task's persistent chat session via the Redis input
    queue; the SDK session feeds it to the agent as the next user-turn.
    """

    type: Literal["chat_message"]
    content: str = Field(min_length=1, max_length=8000)


class WSCloseSession(BaseModel):
    """Inbound: user wants the chat session to wind down gracefully.

    The session honours this between turns (never mid-message) and the
    auto-publisher runs as part of the last turn before close, so any
    in-flight changes are already pushed by the time the close lands.
    """

    type: Literal["close_session"]


class WSPing(BaseModel):
    type: Literal["ping"]


# Discriminated union — pydantic picks the right model from the ``type`` tag.
WSInboundMessage = WSApprovalResponse | WSChatMessage | WSCloseSession | WSPing
