"""ORM models for agent tool configs and MCP server configs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from src.db.models.agent import SubagentSystemTool


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


class UserToolConfig(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Per-user tool overrides on top of the system ``agent_tool_configs`` defaults.

    A row with ``is_enabled=False`` suppresses that tool pattern for the user
    even if the system config has it active. If no row exists for a given
    (user_id, agent_name, subagent_role, tool_pattern) tuple, the system
    default applies.
    """

    __tablename__ = "user_tool_configs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "agent_name", "subagent_role", "tool_pattern",
            name="uq_user_tool_configs_user_agent_role_pattern",
        ),
        Index("ix_user_tool_configs_user_agent", "user_id", "agent_name"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    subagent_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_pattern: Mapped[str] = mapped_column(String(256), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Subagent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A named subagent the orchestrator can delegate to.

    Stores the full AgentDefinition config (description, system_prompt, model)
    that was previously hardcoded in ``OrchestratorAgent.build_subagents()``.
    System tools (Read, Edit, Bash variants) remain hardcoded in Python;
    only MCP tool associations are stored here via ``SubagentTool``.
    """

    __tablename__ = "subagents"
    __table_args__ = (UniqueConstraint("name", name="uq_subagents_name"),)

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(32), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    tools: Mapped[list[SubagentTool]] = relationship(
        back_populates="subagent",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    system_tools: Mapped[list["SubagentSystemTool"]] = relationship(
        "SubagentSystemTool",
        primaryjoin="Subagent.id == foreign(SubagentSystemTool.subagent_id)",
        lazy="selectin",
        viewonly=True,
    )


class SubagentTool(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Allowed MCP tool association between a Subagent and an MCPServerConfig.

    One row means the subagent is allowed to use that MCP integration.
    ``is_active=False`` soft-disables the association without removing the row.
    """

    __tablename__ = "subagent_tools"
    __table_args__ = (
        UniqueConstraint(
            "subagent_id", "mcp_server_config_id",
            name="uq_subagent_tools_subagent_mcp",
        ),
    )

    subagent_id: Mapped[UUID] = mapped_column(
        ForeignKey("subagents.id", ondelete="CASCADE"), nullable=False
    )
    mcp_server_config_id: Mapped[UUID] = mapped_column(
        ForeignKey("mcp_server_configs.id", ondelete="CASCADE"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    subagent: Mapped[Subagent] = relationship(back_populates="tools")
    mcp_server: Mapped[MCPServerConfig] = relationship(lazy="selectin")
