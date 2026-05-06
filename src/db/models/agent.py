"""ORM models for per-user agents, system tools catalog, and link-level MCP overrides.

A user composes their own orchestrator instances by picking subagents from
the admin catalog. Each ``Agent`` is one such composition. ``AgentSubagent``
is the link table between an agent and a subagent, and
``AgentSubagentMcp`` lets the user pick which MCP integrations a subagent
gets inside one specific agent (overriding the admin defaults from
``subagent_tools``).

The ``SystemTool`` / ``SubagentSystemTool`` pair replaces the previously
hardcoded ``_system_tools`` dict that lived inside ``OrchestratorAgent``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, UserScopeMixin

if TYPE_CHECKING:
    from src.db.models.agent_config import MCPServerConfig, Subagent


class SystemTool(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Built-in SDK tool pattern available to subagents (Read, Edit, Bash, ...).

    Admin-managed catalog. Each row is one tool the orchestrator can give a
    subagent via the SDK ``allowed_tools`` list. ``pattern`` is the exact
    string the SDK expects (e.g. ``"Read"``, ``"Bash(git diff*)"``).
    """

    __tablename__ = "system_tools"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    pattern: Mapped[str] = mapped_column(String(256), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SubagentSystemTool(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Admin-defined link between a subagent and the system tools it can use."""

    __tablename__ = "subagent_system_tools"
    __table_args__ = (
        UniqueConstraint(
            "subagent_id", "system_tool_id",
            name="uq_subagent_system_tools_subagent_tool",
        ),
    )

    subagent_id: Mapped[UUID] = mapped_column(
        ForeignKey("subagents.id", ondelete="CASCADE"), nullable=False
    )
    system_tool_id: Mapped[UUID] = mapped_column(
        ForeignKey("system_tools.id", ondelete="CASCADE"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    system_tool: Mapped[SystemTool] = relationship(lazy="selectin")


class Agent(Base, UUIDPrimaryKeyMixin, UserScopeMixin, TimestampMixin):
    """One user-owned orchestrator instance.

    Bundles a name and a chosen subset of subagents (via ``AgentSubagent``).
    ``system_prompt`` and ``model`` are nullable: NULL means the runtime
    falls back to the in-code defaults — kept around so we can A/B-test
    custom prompts/models later without another migration. The user-facing
    API does not expose these fields yet.

    ``is_default`` marks the agent used when a task is created without an
    explicit ``agent_id``. The migration also adds a partial unique index
    enforcing one default per user at the DB level.
    """

    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_agents_user_id_name"),
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    subagents: Mapped[list["AgentSubagent"]] = relationship(
        back_populates="agent",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class AgentSubagent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Link between one ``Agent`` and one ``Subagent``.

    The user attaches a subagent to their agent by inserting one of these.
    ``AgentSubagentMcp`` rows hang off this link to express per-link MCP
    selection.
    """

    __tablename__ = "agent_subagents"
    __table_args__ = (
        UniqueConstraint(
            "agent_id", "subagent_id",
            name="uq_agent_subagents_agent_id_subagent_id",
        ),
        Index("ix_agent_subagents_agent_id", "agent_id"),
    )

    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    subagent_id: Mapped[UUID] = mapped_column(
        ForeignKey("subagents.id", ondelete="CASCADE"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    agent: Mapped[Agent] = relationship(back_populates="subagents")
    subagent: Mapped["Subagent"] = relationship(lazy="selectin")
    mcps: Mapped[list["AgentSubagentMcp"]] = relationship(
        back_populates="link",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class AgentSubagentMcp(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Per-link MCP selection: which integrations this subagent has inside this agent.

    Populated from the admin defaults in ``subagent_tools`` at the moment
    the link is created. The user can then add or remove rows freely
    without affecting other agents that use the same subagent.
    """

    __tablename__ = "agent_subagent_mcps"
    __table_args__ = (
        UniqueConstraint(
            "agent_subagent_id", "mcp_server_config_id",
            name="uq_agent_subagent_mcps_link_mcp",
        ),
        Index("ix_agent_subagent_mcps_link", "agent_subagent_id"),
    )

    agent_subagent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_subagents.id", ondelete="CASCADE"), nullable=False
    )
    mcp_server_config_id: Mapped[UUID] = mapped_column(
        ForeignKey("mcp_server_configs.id", ondelete="CASCADE"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    link: Mapped[AgentSubagent] = relationship(back_populates="mcps")
    mcp_server: Mapped["MCPServerConfig"] = relationship(lazy="selectin")
