"""Azure in-process MCP server and skill-tool classes.

Public surface used by ``OrchestratorAgent.build_in_process_mcp_servers``:

- ``CLYDE_AZURE_SERVER_NAME``: dict-key for ``ClaudeAgentOptions.mcp_servers``.
- ``build_azure_skills_server``: factory bound to a user OAuth token.
"""

from src.agent_tools.custom_tools.azure.server import (
    CLYDE_AZURE_SERVER_NAME,
    build_azure_skills_server,
)

__all__ = ["CLYDE_AZURE_SERVER_NAME", "build_azure_skills_server"]
