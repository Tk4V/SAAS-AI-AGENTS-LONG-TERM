"""GitHub MCP server configuration.

Returns a ``McpStdioServerConfig``-compatible dict for the official
``@modelcontextprotocol/server-github`` package. Pass the result directly
to ``ClaudeAgentOptions.mcp_servers``.
"""

from __future__ import annotations

from typing import Any


def github_mcp_server(token: str, raw_metadata: dict[str, Any]) -> dict[str, object]:
    """Return a stdio MCP server config for GitHub, authenticated with *token*.

    ``raw_metadata`` is accepted for interface uniformity but unused — GitHub
    needs only the access token.

    Args:
        token: A GitHub OAuth or personal access token with ``repo`` scope.
        raw_metadata: Credential metadata stored after OAuth (ignored here).

    Returns:
        A ``McpStdioServerConfig`` dict ready for use in ``ClaudeAgentOptions``.
    """
    return {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": token},
    }
