"""Service for materialising runtime MCP server configs from database records."""

from __future__ import annotations

from typing import Any

from src.db.models.agent_config import MCPServerConfig


class AgentConfigService:
    """Builds ClaudeAgentOptions-compatible MCP server dicts from DB config rows."""

    def build_mcp_server_entry(
        self,
        *,
        config: MCPServerConfig,
        token: str,
    ) -> dict[str, Any]:
        """Materialise one MCP server config by injecting the runtime token.

        Substitutes the ``{token}`` sentinel in every ``header_templates``
        value with the live credential token. Any ``extra_config`` fields are
        merged into the result dict for future extensibility.
        """
        headers = {
            k: v.replace("{token}", token)
            for k, v in config.header_templates.items()
        }
        entry: dict[str, Any] = {
            "type": config.transport_type,
            "url": config.url_template,
            "headers": headers,
        }
        if config.extra_config:
            entry.update(config.extra_config)
        return entry
