from __future__ import annotations

from pydantic import BaseModel


class AgentRead(BaseModel):
    id: str
    name: str
    description: str


class AgentsList(BaseModel):
    items: list[AgentRead]


class ToolRead(BaseModel):
    """One MCP tool entry."""

    tool_name: str        # Provider key, e.g. "github", "jira"
    sort_order: int
    display_name: str     # Human-readable name, e.g. "GitHub"
    category: str         # Integration category, e.g. "vcs", "cloud"


class ToolsList(BaseModel):
    items: list[ToolRead]
