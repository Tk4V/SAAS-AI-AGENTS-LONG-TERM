"""Jira MCP server configuration.

Returns a ``McpStdioServerConfig``-compatible dict for the ``mcp-atlassian``
Python package. Pass the result directly to ``ClaudeAgentOptions.mcp_servers``.

The server is started via ``uvx mcp-atlassian`` which downloads and runs the
package in an isolated environment without polluting the app's virtualenv.
"""

from __future__ import annotations

import sys
from typing import Any


def jira_mcp_server(token: str, raw_metadata: dict[str, Any]) -> dict[str, object]:
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
        raw_metadata: Credential metadata stored after OAuth. Must contain
            ``site_url`` (e.g. ``https://yourcompany.atlassian.net``) and
            ``cloud_id`` (Atlassian cloud ID). Raises ``ValueError`` if either
            is missing — ``BaseAgent.build_user_mcp_servers`` catches this and
            skips the provider with a warning log.

    Returns:
        A ``McpStdioServerConfig`` dict ready for use in ``ClaudeAgentOptions``.
    """
    site_url = raw_metadata.get("site_url") or ""
    cloud_id = raw_metadata.get("cloud_id") or ""
    if not site_url or not cloud_id:
        raise ValueError(
            "Jira raw_metadata is missing 'site_url' or 'cloud_id'. "
            "These are stored during the OAuth callback — reconnect Jira to fix this."
        )
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
