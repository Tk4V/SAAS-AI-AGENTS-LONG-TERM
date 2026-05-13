"""Build the in-process ``clyde_azure`` MCP server.

This is the public entry point used by ``OrchestratorAgent.build_in_process_mcp_servers`` —
``build_azure_skills_server(azure_token=...)`` returns an
``McpSdkServerConfig`` keyed by ``CLYDE_AZURE_SERVER_NAME`` so the SDK
mounts every tool declared in ``tools.py`` under
``mcp__clyde_azure__*``.
"""

from __future__ import annotations

from typing import Any

from src.agent_tools.custom_tools.azure.tools import (
    GetFailedDeploymentLogsTool,
    ListResourceGroupsTool,
    ListSubscriptionsTool,
    ListVirtualMachinesTool,
)
from src.agent_tools.custom_tools.mcp_server_builder import build_mcp_server

CLYDE_AZURE_SERVER_NAME = "clyde_azure"


def build_azure_skills_server(*, azure_token: str) -> Any:
    """Return an in-process MCP server bound to the given Azure OAuth token.

    Each tool listed below is instantiated with the token and assembled
    into an SDK MCP server. Adding a new Azure skill is a one-line
    change here plus a new class in ``tools.py``.
    """
    return build_mcp_server(
        name=CLYDE_AZURE_SERVER_NAME,
        version="1.0.0",
        tools=[
            ListSubscriptionsTool(azure_token),
            ListResourceGroupsTool(azure_token),
            ListVirtualMachinesTool(azure_token),
            GetFailedDeploymentLogsTool(azure_token),
        ],
    )
