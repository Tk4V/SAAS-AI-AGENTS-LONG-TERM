from __future__ import annotations

from pydantic import BaseModel


class AgentRead(BaseModel):
    id: str
    name: str
    description: str


class AgentsList(BaseModel):
    items: list[AgentRead]


class ToolRead(BaseModel):
    """One tool entry as seen by the user."""

    tool_pattern: str
    agent_name: str
    subagent_role: str | None
    sort_order: int
    requires_provider: str | None  # None for built-in tools (Read, Edit, Bash…)
    is_enabled: bool               # Reflects user override; defaults to True


class ToolsList(BaseModel):
    items: list[ToolRead]


class ToolUpdate(BaseModel):
    """Payload for enabling or disabling a tool."""

    agent_name: str
    subagent_role: str | None = None
    tool_pattern: str
    is_enabled: bool
