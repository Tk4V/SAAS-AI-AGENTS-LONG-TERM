"""In-process MCP server exposing chat-with-the-user skills to SDK agents.

Public surface used by ``OrchestratorAgent.build_in_process_mcp_servers``:

- ``CLYDE_CHAT_SERVER_NAME``: dict-key for ``ClaudeAgentOptions.mcp_servers``.
- ``build_chat_skills_server``: factory bound to the current task and agent.
"""

from src.agent_tools.custom_tools.chat.server import (
    CLYDE_CHAT_SERVER_NAME,
    build_chat_skills_server,
)

__all__ = ["CLYDE_CHAT_SERVER_NAME", "build_chat_skills_server"]
