"""Build the in-process ``clyde_google`` MCP server.

Public entry point used by ``OrchestratorAgent.build_in_process_mcp_servers`` —
``build_google_skills_server(google_token=...)`` returns an
``McpSdkServerConfig`` keyed by ``CLYDE_GOOGLE_SERVER_NAME`` so the SDK
mounts every tool declared in ``tools.py`` under ``mcp__clyde_google__*``.

Authentication uses the user's Google OAuth access token stored in the
credentials table. The token is passed to each tool at construction time.
"""

from __future__ import annotations

from typing import Any

from src.agent_tools.custom_tools.google.tools import (
    CreateCalendarEventTool,
    CreateDraftTool,
    CreateMeetMeetingTool,
    GetEmailTool,
    ListCalendarEventsTool,
    SearchEmailsTool,
)
from src.agent_tools.custom_tools.mcp_server_builder import build_mcp_server

CLYDE_GOOGLE_SERVER_NAME = "clyde_google"


def build_google_skills_server(*, google_token: str) -> Any:
    """Return an in-process MCP server bound to the given Google OAuth token.

    Each tool is instantiated with the token and assembled into an SDK MCP
    server. Adding a new Google skill is a one-line change here plus a new
    class in ``tools.py``.
    """
    return build_mcp_server(
        name=CLYDE_GOOGLE_SERVER_NAME,
        version="1.0.0",
        tools=[
            SearchEmailsTool(google_token),
            GetEmailTool(google_token),
            CreateDraftTool(google_token),
            ListCalendarEventsTool(google_token),
            CreateCalendarEventTool(google_token),
            CreateMeetMeetingTool(google_token),
        ],
    )
