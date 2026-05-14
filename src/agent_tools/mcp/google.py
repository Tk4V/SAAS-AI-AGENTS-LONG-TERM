"""Google Workspace MCP server configurations.

Returns ``McpHttpServerConfig``-compatible dicts for Google's official remote
MCP endpoints. Pass the results directly to ``ClaudeAgentOptions.mcp_servers``.

Both servers use the same Google OAuth access token — the token must have the
appropriate scopes (gmail.send, gmail.readonly, calendar, etc.) which are
requested during the Google OAuth consent flow.
"""

from __future__ import annotations

from typing import Any

_GMAIL_MCP_URL = "https://gmailmcp.googleapis.com/mcp/v1"
_CALENDAR_MCP_URL = "https://calendarmcp.googleapis.com/mcp/v1"


def gmail_mcp_server(token: str, raw_metadata: dict[str, Any]) -> dict[str, object]:
    """Return a Streamable HTTP MCP server config for Google's Gmail MCP endpoint.

    Args:
        token: A Google OAuth access token with ``gmail.send``,
            ``gmail.readonly``, and ``gmail.compose`` scopes.
        raw_metadata: Credential metadata stored after OAuth (unused here).

    Returns:
        A ``McpHttpServerConfig`` dict ready for use in ``ClaudeAgentOptions``.
    """
    return {
        "type": "http",
        "url": _GMAIL_MCP_URL,
        "headers": {"Authorization": f"Bearer {token}"},
    }


def calendar_mcp_server(token: str, raw_metadata: dict[str, Any]) -> dict[str, object]:
    """Return a Streamable HTTP MCP server config for Google's Calendar MCP endpoint.

    Args:
        token: A Google OAuth access token with ``calendar`` and
            ``calendar.events`` scopes.
        raw_metadata: Credential metadata stored after OAuth (unused here).

    Returns:
        A ``McpHttpServerConfig`` dict ready for use in ``ClaudeAgentOptions``.
    """
    return {
        "type": "http",
        "url": _CALENDAR_MCP_URL,
        "headers": {"Authorization": f"Bearer {token}"},
    }
