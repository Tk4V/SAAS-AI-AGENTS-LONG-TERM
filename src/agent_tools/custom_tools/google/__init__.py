"""Google Workspace in-process MCP server and skill-tool classes.

Public surface used by ``OrchestratorAgent.build_in_process_mcp_servers``:

- ``CLYDE_GOOGLE_SERVER_NAME``: dict-key for ``ClaudeAgentOptions.mcp_servers``.
- ``build_google_skills_server``: factory bound to a user Google OAuth token.
"""

from src.agent_tools.custom_tools.google.server import (
    CLYDE_GOOGLE_SERVER_NAME,
    build_google_skills_server,
)

__all__ = ["CLYDE_GOOGLE_SERVER_NAME", "build_google_skills_server"]
