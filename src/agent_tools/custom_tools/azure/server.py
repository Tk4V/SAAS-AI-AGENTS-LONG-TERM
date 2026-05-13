"""Build the in-process ``clyde_azure`` MCP server.

This is the public entry point used by ``OrchestratorAgent.build_in_process_mcp_servers`` —
``build_azure_skills_server(credentials=...)`` returns an
``McpSdkServerConfig`` keyed by ``CLYDE_AZURE_SERVER_NAME`` so the SDK
mounts every tool declared in ``tools.py`` under
``mcp__clyde_azure__*``.

Authentication uses a service principal stored as a BEARER JSON credential:
``{"client_id": "...", "client_secret": "...", "tenant_id": "...", "subscription_id": "..."}``.
The credentials are passed to each tool at construction time and injected
as environment variables into every ``az`` subprocess call.
"""

from __future__ import annotations

from typing import Any

from src.agent_tools.custom_tools.azure.tools import (
    ConnectAzureTool,
    ListResourceGroupsTool,
    ListSubscriptionsTool,
    ListVirtualMachinesTool,
    RunAzCommandTool,
)
from src.agent_tools.custom_tools.mcp_server_builder import build_mcp_server

CLYDE_AZURE_SERVER_NAME = "clyde_azure"


def build_azure_skills_server(*, credentials: dict[str, str]) -> Any:
    """Return an in-process MCP server bound to the given service principal credentials.

    Each tool is instantiated with the credentials dict and assembled into
    an SDK MCP server. Adding a new Azure skill is a one-line change here
    plus a new class in ``tools.py``.
    """
    return build_mcp_server(
        name=CLYDE_AZURE_SERVER_NAME,
        version="1.0.0",
        tools=[
            ConnectAzureTool(credentials),
            RunAzCommandTool(credentials),
            ListSubscriptionsTool(credentials),
            ListResourceGroupsTool(credentials),
            ListVirtualMachinesTool(credentials),
        ],
    )
