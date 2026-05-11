"""Build the in-process ``clyde_chat`` MCP server.

Bound to a specific task and agent so the ``ask_user`` tool can route the
question through the right approval / WebSocket / chat-history pipeline.
Mounted by ``BaseAgent.build_in_process_mcp_servers``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.agent_tools.custom_tools.chat.tools import AskUserTool
from src.agent_tools.custom_tools.mcp_server_builder import build_mcp_server

CLYDE_CHAT_SERVER_NAME = "clyde_chat"


def build_chat_skills_server(
    *,
    task_id: UUID,
    user_id: int,
    agent_name: str,
) -> Any:
    """Return an MCP server bound to the current task / agent context."""
    return build_mcp_server(
        name=CLYDE_CHAT_SERVER_NAME,
        version="1.0.0",
        tools=[
            AskUserTool(task_id=task_id, user_id=user_id, agent_name=agent_name),
        ],
    )
