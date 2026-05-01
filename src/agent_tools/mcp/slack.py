"""Slack MCP server configuration.

Returns a ``McpHttpServerConfig``-compatible dict for Slack's official remote
MCP server. Pass the result directly to ``ClaudeAgentOptions.mcp_servers``.
"""

from __future__ import annotations

from typing import Any

from src.config.settings import get_settings


def slack_mcp_server(token: str, raw_metadata: dict[str, Any]) -> dict[str, object]:
    """Return a Streamable HTTP MCP server config for Slack's official remote MCP, authenticated with *token*.

    Slack's MCP server uses Streamable HTTP transport (``type: "http"``), not
    SSE — unlike GitHub's MCP server. Using ``type: "sse"`` causes the SDK to
    silently drop all Slack tools.

    ``raw_metadata`` is accepted for interface uniformity but unused — Slack
    needs only the access token.

    Args:
        token: A Slack OAuth bot token (``xoxb-…``) with the scopes configured
            on the app (``channels:read``, ``chat:write``, ``users:read`` at
            minimum).
        raw_metadata: Credential metadata stored after OAuth (ignored here).

    Returns:
        A ``McpHttpServerConfig`` dict ready for use in ``ClaudeAgentOptions``.
    """
    settings = get_settings()
    return {
        "type": "http",
        "url": settings.slack_mcp_url,
        "headers": {"Authorization": f"Bearer {token}"},
    }
