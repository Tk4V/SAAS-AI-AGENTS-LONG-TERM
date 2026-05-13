"""Azure MCP server configuration.

Returns a ``McpSSEServerConfig``-compatible dict for the Azure remote MCP
server, authenticated with an Azure AD OAuth access token.
"""

from __future__ import annotations

from typing import Any

from src.config.settings import get_settings


def azure_mcp_server(token: str, raw_metadata: dict[str, Any]) -> dict[str, object]:
    """Return an SSE MCP server config for the Azure remote MCP.

    Args:
        token: Azure AD OAuth access token scoped to
            ``https://management.azure.com/.default``.
        raw_metadata: Credential metadata stored after OAuth (unused here).

    Returns:
        A ``McpSSEServerConfig`` dict ready for use in ``ClaudeAgentOptions``.
    """
    settings = get_settings()
    return {
        "type": "sse",
        "url": settings.azure_mcp_url,
        "headers": {"Authorization": f"Bearer {token}"},
    }
