"""Jira MCP server configuration.

Returns a ``McpStdioServerConfig``-compatible dict for the ``mcp-atlassian``
Python package. Pass the result directly to ``ClaudeAgentOptions.mcp_servers``.

The server is started via ``uvx mcp-atlassian`` which downloads and runs the
package in an isolated environment without polluting the app's virtualenv.
"""

from __future__ import annotations


def jira_mcp_server(token: str, site_url: str) -> dict[str, object]:
    """Return a stdio MCP server config for Jira, authenticated with *token*.

    Args:
        token: Atlassian OAuth access token with ``read:jira-work`` and
            ``read:jira-user`` scopes.
        site_url: The Jira cloud base URL, e.g. ``https://yourcompany.atlassian.net``.
            Stored in ``user_oauth_credentials.raw_metadata["site_url"]`` during
            the OAuth callback.

    Returns:
        A ``McpStdioServerConfig`` dict ready for use in ``ClaudeAgentOptions``.
    """
    return {
        "type": "stdio",
        "command": "mcp-atlassian",
        "args": [],
        "env": {
            "JIRA_URL": site_url,
            "JIRA_OAUTH_ACCESS_TOKEN": token,
        },
    }
