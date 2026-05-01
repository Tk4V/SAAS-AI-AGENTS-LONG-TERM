"""Jira MCP server configuration.

Returns an ``McpHttpServerConfig``-compatible dict for the official Atlassian
remote MCP server. Pass the result directly to ``ClaudeAgentOptions.mcp_servers``.

The server accepts standard Bearer tokens issued by ``auth.atlassian.com`` and
resolves the correct Jira instance from the token automatically — no cloud_id
or site_url needed.
"""

from __future__ import annotations

from typing import Any

from src.config.settings import get_settings


def jira_mcp_server(token: str, raw_metadata: dict[str, Any]) -> dict[str, object]:
    """Return an HTTP MCP server config for Jira, authenticated with *token*.

    Points at the official Atlassian remote MCP server which accepts Bearer
    tokens issued by ``auth.atlassian.com`` (our existing OAuth flow).

    Args:
        token: Atlassian OAuth access token with ``read:jira-work``,
            ``write:jira-work``, and ``read:jira-user`` scopes.
        raw_metadata: Unused — the remote server resolves the Jira instance
            from the token automatically.

    Returns:
        An ``McpHttpServerConfig`` dict ready for use in ``ClaudeAgentOptions``.
    """
    settings = get_settings()
    return {
        "type": "http",
        "url": settings.jira_mcp_url,
        "headers": {"Authorization": f"Bearer {token}"},
    }
