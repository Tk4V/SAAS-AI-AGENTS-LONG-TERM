"""Jira MCP server configuration.

Returns a ``McpStdioServerConfig``-compatible dict for the ``mcp-atlassian``
Python package. Pass the result directly to ``ClaudeAgentOptions.mcp_servers``.

The server is started via ``uvx mcp-atlassian`` which downloads and runs the
package in an isolated environment without polluting the app's virtualenv.
"""

from __future__ import annotations

import sys


def jira_mcp_server(token: str, site_url: str, cloud_id: str) -> dict[str, object]:
    """Return a stdio MCP server config for Jira, authenticated with *token*.

    Uses ``sys.executable -c "from mcp_atlassian import main; main()"`` —
    the Python equivalent of ``npx -y @modelcontextprotocol/server-github``.
    Invoking via the known interpreter avoids PATH and shebang issues in Docker.

    The env var names match what ``mcp-atlassian`` actually reads:
    - ``JIRA_URL``                    → base URL of the Jira cloud instance
    - ``ATLASSIAN_OAUTH_CLOUD_ID``    → cloud ID for the BYO-access-token OAuth path
    - ``ATLASSIAN_OAUTH_ACCESS_TOKEN``→ the pre-issued OAuth access token

    Args:
        token: Atlassian OAuth access token with ``read:jira-work``,
            ``write:jira-work``, and ``read:jira-user`` scopes.
        site_url: Jira cloud base URL, e.g. ``https://yourcompany.atlassian.net``.
        cloud_id: Atlassian cloud ID stored in
            ``user_oauth_credentials.raw_metadata["cloud_id"]`` after OAuth.

    Returns:
        A ``McpStdioServerConfig`` dict ready for use in ``ClaudeAgentOptions``.
    """
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-c", "from mcp_atlassian import main; main()"],
        "env": {
            "JIRA_URL": site_url,
            "ATLASSIAN_OAUTH_CLOUD_ID": cloud_id,
            "ATLASSIAN_OAUTH_ACCESS_TOKEN": token,
        },
    }
