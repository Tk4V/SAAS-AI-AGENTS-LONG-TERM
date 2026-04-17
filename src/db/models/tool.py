"""ToolRecord ORM model.

Stores user-configured MCP server connections. Sensitive credentials live in
`credentials_encrypted` and are decrypted only when the MCP manager opens a
session. Marked as a Milestone 2 feature; the table exists in M1 so we do not
need a schema change later.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, UserScopeMixin


class ToolKind(str, enum.Enum):
    MCP = "mcp"
    HTTP = "http"


class ToolRecord(Base, UUIDPrimaryKeyMixin, UserScopeMixin, TimestampMixin):
    __tablename__ = "tools"
    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_tools_user_id_slug"),
    )

    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[ToolKind] = mapped_column(
        Enum(ToolKind, name="tool_kind", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=ToolKind.MCP,
    )
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    credentials_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
