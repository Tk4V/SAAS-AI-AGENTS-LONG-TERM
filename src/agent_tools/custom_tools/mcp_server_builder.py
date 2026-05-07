"""Generic builder for in-process MCP servers from skill-tool classes.

Each provider package under ``custom_tools/<provider>/`` declares its
tools as subclasses of ``BaseSkillTool`` and assembles them into an MCP
server config via ``build_mcp_server``. The builder owns the ``@tool``
wrapping and ``create_sdk_mcp_server`` call so individual provider
modules stay declarative — adding a new tool is one new class, not a
re-write of the server factory.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from claude_agent_sdk import create_sdk_mcp_server, tool


class BaseSkillTool(ABC):
    """Single in-process MCP tool with metadata and async implementation.

    Concrete subclasses set the three class-level fields and implement
    ``run``. Per-session state (auth tokens, user ids, …) is captured in
    ``__init__`` and held on the instance — the same class can be reused
    across sessions by passing different constructor arguments.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]

    @abstractmethod
    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        """Execute the tool against ``args`` and return an MCP content dict.

        Standard return shape:
        ``{"content": [{"type": "text", "text": "..."}]}``.
        """

    def to_sdk_tool(self) -> Any:
        """Wrap this instance with the SDK ``@tool`` decorator.

        Captures the instance in a closure so the resulting tool calls
        back into ``self.run``. The decorator metadata (name,
        description, schema) comes from the class attributes — concrete
        subclasses do not override this.
        """

        @tool(self.name, self.description, self.input_schema)
        async def _wrapped(args: dict[str, Any]) -> dict[str, Any]:
            return await self.run(args)

        return _wrapped


def build_mcp_server(
    *,
    name: str,
    version: str = "1.0.0",
    tools: list[BaseSkillTool],
) -> Any:
    """Assemble an in-process MCP server from a list of tool instances.

    Returns an ``McpSdkServerConfig`` ready to drop into
    ``ClaudeAgentOptions.mcp_servers`` keyed by ``name``. From the agent's
    perspective each tool appears as ``mcp__<name>__<tool.name>``.
    """
    return create_sdk_mcp_server(
        name=name,
        version=version,
        tools=[t.to_sdk_tool() for t in tools],
    )
