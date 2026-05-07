"""GitHub in-process MCP server and skill-tool classes.

Public surface used by ``OrchestratorAgent.build_in_process_mcp_servers``:

- ``CLYDE_GITHUB_SERVER_NAME``: dict-key for ``ClaudeAgentOptions.mcp_servers``.
- ``build_github_skills_server``: factory bound to a user OAuth token.
"""

from src.agent_tools.custom_tools.github.server import (
    CLYDE_GITHUB_SERVER_NAME,
    build_github_skills_server,
)

__all__ = ["CLYDE_GITHUB_SERVER_NAME", "build_github_skills_server"]
