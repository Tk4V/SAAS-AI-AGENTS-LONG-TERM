"""ORM models for agent tool configs and MCP server configs."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentToolConfig(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Allowed tool patterns per agent / subagent role, stored in the database.

    Replaces the hardcoded ``SDK_ALLOWED_TOOLS`` class variable and the
    ``tools=[...]`` lists in ``build_subagents()``. Each row is one pattern
    (e.g. ``"Read"``, ``"mcp__github__*"``) for a specific agent and optional
    subagent role. Rows with ``subagent_role=NULL`` apply to the top-level agent.
    """

    __tablename__ = "agent_tool_configs"
    __table_args__ = (
        Index("ix_agent_tool_configs_agent_name", "agent_name"),
        Index("ix_agent_tool_configs_agent_subagent", "agent_name", "subagent_role"),
    )

    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    subagent_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_pattern: Mapped[str] = mapped_column(String(256), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class MCPServerConfig(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Connection config for an MCP server provider, stored in the database.

    Replaces the hardcoded factory functions in ``src/agent_tools/mcp/``.
    ``header_templates`` is a JSONB dict where ``{token}`` is a placeholder
    substituted with the live credential token at runtime — for example
    ``{"Authorization": "Bearer {token}"}``.
    """

    __tablename__ = "mcp_server_configs"

    provider_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    transport_type: Mapped[str] = mapped_column(String(16), nullable=False)
    url_template: Mapped[str] = mapped_column(String(512), nullable=False)
    header_templates: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="'{}'::jsonb",
        default=dict,
    )
    extra_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="'{}'::jsonb",
        default=dict,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
