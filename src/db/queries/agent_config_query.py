"""Database access for agent tool configs and MCP server configs."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.agent_config import AgentToolConfig, MCPServerConfig


class AgentConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_tool_patterns(
        self,
        *,
        agent_name: str,
        subagent_role: str | None = None,
    ) -> list[str]:
        """Return ordered active tool patterns for the given agent / subagent role.

        Pass ``subagent_role=None`` to fetch the top-level agent's patterns.
        Returns an empty list when no active rows exist (callers should fall
        back to their hardcoded defaults in that case).
        """
        if subagent_role is None:
            role_filter = AgentToolConfig.subagent_role.is_(None)
        else:
            role_filter = AgentToolConfig.subagent_role == subagent_role

        stmt = (
            select(AgentToolConfig.tool_pattern)
            .where(
                AgentToolConfig.agent_name == agent_name,
                role_filter,
                AgentToolConfig.is_active.is_(True),
            )
            .order_by(AgentToolConfig.sort_order, AgentToolConfig.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_active_mcp_configs(self) -> list[MCPServerConfig]:
        """Return all active MCP server configs keyed by provider."""
        stmt = select(MCPServerConfig).where(MCPServerConfig.is_active.is_(True))
        return list((await self._session.execute(stmt)).scalars().all())
