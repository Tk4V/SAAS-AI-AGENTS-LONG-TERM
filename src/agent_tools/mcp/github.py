"""GitHub MCP server configuration.

Returns a ``McpSSEServerConfig``-compatible dict for GitHub's official remote
MCP server. Pass the result directly to ``ClaudeAgentOptions.mcp_servers``.
"""

from __future__ import annotations

from typing import Any

from src.config.settings import get_settings


def github_mcp_server(token: str, raw_metadata: dict[str, Any]) -> dict[str, object]:
    """Return an SSE MCP server config for GitHub's official remote MCP, authenticated with *token*.

    ``raw_metadata`` is accepted for interface uniformity but unused — GitHub
    needs only the access token.

    Args:
        token: A GitHub OAuth or personal access token with ``repo`` scope.
        raw_metadata: Credential metadata stored after OAuth (ignored here).

    Returns:
        A ``McpSSEServerConfig`` dict ready for use in ``ClaudeAgentOptions``.
    """
    settings = get_settings()
    return {
        "type": "sse",
        "url": settings.github_mcp_url,
        "headers": {"Authorization": f"Bearer {token}"},
    }
