"""Database access for agent tool configs and MCP server configs."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.agent_config import AgentToolConfig, MCPServerConfig, UserToolConfig


class AgentConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_tool_patterns(
        self,
        *,
        agent_name: str,
        subagent_role: str | None = None,
    ) -> list[str]:
        """Return ordered active system tool patterns for the given agent / subagent role.

        Pass ``subagent_role=None`` to fetch the top-level agent's patterns.
        Returns an empty list when no active rows exist.
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

    async def get_effective_tool_patterns(
        self,
        *,
        user_id: int | None,
        agent_name: str,
        subagent_role: str | None = None,
    ) -> list[str]:
        """Return tool patterns for a user, applying their personal overrides.

        Loads the system defaults from ``agent_tool_configs``, then filters out
        any patterns the user has explicitly disabled in ``user_tool_configs``.
        When ``user_id`` is None, returns system defaults unchanged.
        """
        system_patterns = await self.get_tool_patterns(
            agent_name=agent_name, subagent_role=subagent_role
        )
        if not user_id or not system_patterns:
            return system_patterns

        disabled = await self._get_user_disabled_patterns(
            user_id=user_id, agent_name=agent_name, subagent_role=subagent_role
        )
        if not disabled:
            return system_patterns
        return [p for p in system_patterns if p not in disabled]

    async def list_active_mcp_configs(self) -> list[MCPServerConfig]:
        """Return all active MCP server configs."""
        stmt = select(MCPServerConfig).where(MCPServerConfig.is_active.is_(True))
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_tools_for_user(
        self,
        *,
        user_id: int,
        agent_name: str | None = None,
        subagent_role: str | None = None,
    ) -> list[dict]:
        """Return all system tool entries enriched with user preferences and provider info.

        Each dict carries:
          - tool_pattern, agent_name, subagent_role, sort_order
          - requires_provider: provider name extracted from mcp__<provider>__* patterns, else None
          - is_enabled: False if the user has disabled it, True otherwise
        """
        # Load system tools
        filters = [AgentToolConfig.is_active.is_(True)]
        if agent_name is not None:
            filters.append(AgentToolConfig.agent_name == agent_name)
            if subagent_role is None and agent_name is not None:
                # Only filter subagent_role when agent_name is also specified
                pass

        stmt = (
            select(AgentToolConfig)
            .where(*filters)
            .order_by(AgentToolConfig.agent_name, AgentToolConfig.subagent_role, AgentToolConfig.sort_order)
        )
        system_rows = list((await self._session.execute(stmt)).scalars().all())

        # Load MCP provider names for requires_provider lookup
        mcp_stmt = select(MCPServerConfig.provider_name).where(MCPServerConfig.is_active.is_(True))
        mcp_providers: set[str] = set((await self._session.execute(mcp_stmt)).scalars().all())

        # Load user-level overrides
        user_stmt = select(UserToolConfig).where(UserToolConfig.user_id == user_id)
        user_rows = (await self._session.execute(user_stmt)).scalars().all()
        user_map: dict[tuple, bool] = {
            (r.agent_name, r.subagent_role, r.tool_pattern): r.is_enabled
            for r in user_rows
        }

        results = []
        for row in system_rows:
            provider = _extract_provider(row.tool_pattern, mcp_providers)
            key = (row.agent_name, row.subagent_role, row.tool_pattern)
            is_enabled = user_map.get(key, True)
            results.append({
                "tool_pattern": row.tool_pattern,
                "agent_name": row.agent_name,
                "subagent_role": row.subagent_role,
                "sort_order": row.sort_order,
                "requires_provider": provider,
                "is_enabled": is_enabled,
            })
        return results

    async def upsert_user_tool(
        self,
        *,
        user_id: int,
        agent_name: str,
        subagent_role: str | None,
        tool_pattern: str,
        is_enabled: bool,
    ) -> UserToolConfig:
        """Insert or update a user tool preference row."""
        stmt = (
            insert(UserToolConfig)
            .values(
                user_id=user_id,
                agent_name=agent_name,
                subagent_role=subagent_role,
                tool_pattern=tool_pattern,
                is_enabled=is_enabled,
            )
            .on_conflict_do_update(
                constraint="uq_user_tool_configs_user_agent_role_pattern",
                set_={"is_enabled": is_enabled},
            )
            .returning(UserToolConfig)
        )
        result = (await self._session.execute(stmt)).scalar_one()
        return result

    async def _get_user_disabled_patterns(
        self,
        *,
        user_id: int,
        agent_name: str,
        subagent_role: str | None,
    ) -> set[str]:
        """Return the set of tool patterns the user has explicitly disabled."""
        if subagent_role is None:
            role_filter = UserToolConfig.subagent_role.is_(None)
        else:
            role_filter = UserToolConfig.subagent_role == subagent_role

        stmt = select(UserToolConfig.tool_pattern).where(
            UserToolConfig.user_id == user_id,
            UserToolConfig.agent_name == agent_name,
            role_filter,
            UserToolConfig.is_enabled.is_(False),
        )
        return set((await self._session.execute(stmt)).scalars().all())


def _extract_provider(pattern: str, known_providers: set[str]) -> str | None:
    """Extract the provider name from an MCP tool pattern like ``mcp__github__*``.

    Returns None for built-in patterns (Read, Edit, Bash, etc.).
    """
    if not pattern.startswith("mcp__"):
        return None
    parts = pattern.split("__")
    if len(parts) >= 2:
        candidate = parts[1]
        return candidate if candidate in known_providers else candidate
    return None
