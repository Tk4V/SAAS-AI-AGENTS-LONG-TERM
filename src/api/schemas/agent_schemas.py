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


class SubagentToolRead(BaseModel):
    """One MCP tool allowed for a subagent."""

    tool_name: str     # "mcp__github__*"
    display_name: str  # "GitHub"
    category: str      # "vcs"
    is_active: bool


class SubagentRead(BaseModel):
    """One subagent entry."""

    name: str           # "code-implementer"
    display_name: str   # "Code Implementer"
    description: str
    system_prompt: str
    model: str          # "sonnet" | "haiku"
    sort_order: int
    is_active: bool
    tools: list[SubagentToolRead]


class SubagentsList(BaseModel):
    items: list[SubagentRead]


class SubagentToolUpdate(BaseModel):
    """Payload for enabling or disabling an MCP tool on a subagent."""

    mcp_provider: str  # provider name, e.g. "github"
    is_active: bool
