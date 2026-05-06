"""Pydantic schemas for the agent / subagent / system-tool endpoints.

Per-user agent shapes live at the top, admin-side subagent CRUD at the
bottom. ``ToolRead`` / ``ToolsList`` are kept for the existing tools view.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from src.db.models.agent import (
        Agent,
        AgentSubagent,
        AgentSubagentMcp,
        SubagentSystemTool,
        SystemTool,
    )
    from src.db.models.agent_config import MCPServerConfig, Subagent


# ── Generic tool schemas (used by /tools view, kept for backwards compat) ────


class ToolRead(BaseModel):
    """One MCP tool entry shown by the public /tools listing.

    ``id`` is the ``mcp_server_config_id`` end-users pass to
    ``POST /agents/{agent_id}/subagents/{subagent_id}/mcps/{mcp_id}``.
    """

    id: UUID
    tool_name: str
    sort_order: int
    display_name: str
    category: str


class ToolsList(BaseModel):
    items: list[ToolRead]


class SubagentToolRead(BaseModel):
    """One MCP tool entry allowed for a subagent (admin defaults).

    ``mcp_server_config_id`` is exposed so the UI can correlate the
    inherited default with the corresponding MCP override the user can
    add or remove via the **Agent MCPs** endpoints.
    """

    mcp_server_config_id: UUID
    tool_name: str
    display_name: str
    category: str
    is_active: bool


class SubagentToolUpdate(BaseModel):
    """Body of the legacy ``PATCH /subagents/{name}`` endpoint."""

    mcp_provider: str
    is_active: bool


# ── User-side: agents ────────────────────────────────────────────────────────


class AgentSubagentMcpRead(BaseModel):
    """One MCP integration enabled for a subagent inside an agent."""

    model_config = ConfigDict(from_attributes=True)

    mcp_server_config_id: UUID
    provider_name: str
    is_active: bool

    @classmethod
    def from_orm(cls, link_mcp: "AgentSubagentMcp") -> "AgentSubagentMcpRead":
        return cls(
            mcp_server_config_id=link_mcp.mcp_server_config_id,
            provider_name=link_mcp.mcp_server.provider_name,
            is_active=link_mcp.is_active,
        )


class AgentSubagentRead(BaseModel):
    """One subagent linked to an agent, with the user's per-link MCP set."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subagent_id: UUID
    name: str
    display_name: str
    description: str
    sort_order: int
    is_active: bool
    mcps: list[AgentSubagentMcpRead]

    @classmethod
    def from_orm(cls, link: "AgentSubagent") -> "AgentSubagentRead":
        return cls(
            id=link.id,
            subagent_id=link.subagent_id,
            name=link.subagent.name,
            display_name=link.subagent.display_name,
            description=link.subagent.description,
            sort_order=link.sort_order,
            is_active=link.is_active,
            mcps=[AgentSubagentMcpRead.from_orm(m) for m in link.mcps],
        )


class AgentRead(BaseModel):
    """Full agent payload returned by ``GET /agents/{id}``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    display_name: str
    description: str | None
    is_default: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    subagents: list[AgentSubagentRead]

    @classmethod
    def from_orm(cls, agent: "Agent") -> "AgentRead":
        return cls(
            id=agent.id,
            name=agent.name,
            display_name=agent.display_name,
            description=agent.description,
            is_default=agent.is_default,
            is_active=agent.is_active,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
            subagents=[
                AgentSubagentRead.from_orm(link)
                for link in sorted(agent.subagents, key=lambda l: l.sort_order)
            ],
        )


class AgentListItem(BaseModel):
    """Slim agent shape for ``GET /agents``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    display_name: str
    description: str | None
    is_default: bool
    is_active: bool
    subagent_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, agent: "Agent") -> "AgentListItem":
        return cls(
            id=agent.id,
            name=agent.name,
            display_name=agent.display_name,
            description=agent.description,
            is_default=agent.is_default,
            is_active=agent.is_active,
            subagent_count=len(agent.subagents) if agent.subagents else 0,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )


class AgentsList(BaseModel):
    items: list[AgentListItem]


class AgentCreate(BaseModel):
    """Body for ``POST /agents``.

    ``subagent_ids`` must be non-empty: an orchestrator without any
    subagents has nothing to delegate to. Validation is enforced at the
    service layer too — this Field constraint is just for fast feedback.
    """

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    display_name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    subagent_ids: list[UUID] = Field(min_length=1)
    is_default: bool = False


class AgentUpdate(BaseModel):
    name: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$",
    )
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    is_default: bool | None = None


# ── Admin-side: subagents and system-tool catalog ────────────────────────────


class SystemToolRead(BaseModel):
    """One built-in SDK tool entry from the admin catalog."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    display_name: str
    description: str
    category: str
    pattern: str
    is_active: bool

    @classmethod
    def from_orm(cls, tool: "SystemTool") -> "SystemToolRead":
        return cls(
            id=tool.id,
            name=tool.name,
            display_name=tool.display_name,
            description=tool.description,
            category=tool.category,
            pattern=tool.pattern,
            is_active=tool.is_active,
        )


class SystemToolsList(BaseModel):
    items: list[SystemToolRead]


class MCPServerRead(BaseModel):
    """One admin-configured MCP server entry."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider_name: str
    transport_type: str
    is_active: bool

    @classmethod
    def from_orm(cls, cfg: "MCPServerConfig") -> "MCPServerRead":
        return cls(
            id=cfg.id,
            provider_name=cfg.provider_name,
            transport_type=cfg.transport_type,
            is_active=cfg.is_active,
        )


class MCPServersList(BaseModel):
    items: list[MCPServerRead]


class SubagentSystemToolRead(BaseModel):
    """Admin link between a subagent and one system tool."""

    model_config = ConfigDict(from_attributes=True)

    system_tool_id: UUID
    name: str
    display_name: str
    pattern: str
    is_active: bool

    @classmethod
    def from_orm(cls, link: "SubagentSystemTool") -> "SubagentSystemToolRead":
        return cls(
            system_tool_id=link.system_tool_id,
            name=link.system_tool.name,
            display_name=link.system_tool.display_name,
            pattern=link.system_tool.pattern,
            is_active=link.is_active,
        )


class SubagentRead(BaseModel):
    """Public subagent catalog entry — what users see when picking subagents.

    ``id`` is included so the frontend can pass it to ``POST /agents``
    or to ``POST /agents/{id}/subagents/{subagent_id}``.
    ``tools`` is the admin-default MCP set (from ``subagent_tools``)
    that newly attached links inherit. ``system_tools`` is the admin-
    controlled built-in SDK tools (Read, Bash, ...). Both are read-only
    in the public catalog; only admins mutate them via ``/admin/subagents``.
    """

    id: UUID
    name: str
    display_name: str
    description: str
    system_prompt: str
    model: str
    sort_order: int
    is_active: bool
    tools: list[SubagentToolRead]
    system_tools: list[SubagentSystemToolRead] = Field(default_factory=list)


class SubagentsList(BaseModel):
    items: list[SubagentRead]


class SubagentDetail(BaseModel):
    """Admin-only detailed view of a subagent (with id and full system tools)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    display_name: str
    description: str
    system_prompt: str
    model: str
    sort_order: int
    is_active: bool
    system_tools: list[SubagentSystemToolRead]

    @classmethod
    def from_orm(cls, subagent: "Subagent") -> "SubagentDetail":
        return cls(
            id=subagent.id,
            name=subagent.name,
            display_name=subagent.display_name,
            description=subagent.description,
            system_prompt=subagent.system_prompt,
            model=subagent.model,
            sort_order=subagent.sort_order,
            is_active=subagent.is_active,
            system_tools=[
                SubagentSystemToolRead.from_orm(st)
                for st in (subagent.system_tools or [])
            ],
        )


class SubagentAdminCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=32)
    sort_order: int = 0
    is_active: bool = True
    system_tool_ids: list[UUID] = Field(default_factory=list)
    mcp_server_config_ids: list[UUID] = Field(default_factory=list)


class SubagentAdminUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    system_prompt: str | None = None
    model: str | None = Field(default=None, min_length=1, max_length=32)
    sort_order: int | None = None
    is_active: bool | None = None
    system_tool_ids: list[UUID] | None = None
    mcp_server_config_ids: list[UUID] | None = None
