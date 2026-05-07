"""Build the in-process ``clyde_github`` MCP server.

This is the public entry point used by ``OrchestratorAgent.build_in_process_mcp_servers`` —
``build_github_skills_server(github_token=...)`` returns an
``McpSdkServerConfig`` keyed by ``CLYDE_GITHUB_SERVER_NAME`` so the SDK
mounts every tool declared in ``tools.py`` under
``mcp__clyde_github__*``.
"""

from __future__ import annotations

from typing import Any

from src.agent_tools.custom_tools.github.tools import GetFailedCILogsTool
from src.agent_tools.custom_tools.mcp_server_builder import build_mcp_server

CLYDE_GITHUB_SERVER_NAME = "clyde_github"


def build_github_skills_server(*, github_token: str) -> Any:
    """Return an in-process MCP server bound to the given GitHub OAuth token.

    Each tool listed below is instantiated with the token and assembled
    into an SDK MCP server. Adding a new GitHub skill is a one-line
    change here plus a new class in ``tools.py``.
    """
    return build_mcp_server(
        name=CLYDE_GITHUB_SERVER_NAME,
        version="1.0.0",
        tools=[
            GetFailedCILogsTool(github_token),
        ],
    )
