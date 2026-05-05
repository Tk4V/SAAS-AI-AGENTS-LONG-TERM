"""User-facing tools endpoint.

Lists all available tools from the system catalog, enriched with the
authenticated user's preferences (enabled/disabled overrides). Users can
enable or disable individual tools via PUT /tools.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.api.dependencies import AgentConfigRepositoryDep, CurrentUserDep
from src.api.schemas.agent_schemas import ToolRead, ToolsList, ToolUpdate

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolsView:
    """Read and manage per-user tool preferences."""

    @staticmethod
    @router.get("", response_model=ToolsList)
    async def list(
        user: CurrentUserDep,
        repo: AgentConfigRepositoryDep,
        agent_name: str | None = None,
        subagent_role: str | None = None,
    ) -> ToolsList:
        """List all available tools with the user's current enabled/disabled state.

        Optionally filter by ``agent_name`` and ``subagent_role``.
        ``requires_provider`` is non-null for MCP-backed tools; null for
        built-in tools like Read, Edit, and Bash variants.
        """
        rows = await repo.list_tools_for_user(
            user_id=user.id,
            agent_name=agent_name,
            subagent_role=subagent_role,
        )
        return ToolsList(items=[ToolRead(**row) for row in rows])

    @staticmethod
    @router.patch("", response_model=ToolRead)
    async def update(
        payload: ToolUpdate,
        user: CurrentUserDep,
        repo: AgentConfigRepositoryDep,
    ) -> ToolRead:
        """Enable or disable a tool for the authenticated user.

        Upserts a row in ``user_tool_configs``. The change takes effect on the
        next agent run — running sessions are not affected.
        """
        await repo.upsert_user_tool(
            user_id=user.id,
            agent_name=payload.agent_name,
            subagent_role=payload.subagent_role,
            tool_pattern=payload.tool_pattern,
            is_enabled=payload.is_enabled,
        )
        # Re-fetch to return up-to-date enriched view
        rows = await repo.list_tools_for_user(user_id=user.id, agent_name=payload.agent_name)
        match = next(
            (r for r in rows if r["tool_pattern"] == payload.tool_pattern and r["subagent_role"] == payload.subagent_role),
            None,
        )
        if match is None:
            # Pattern not in system catalog — still return what we set
            return ToolRead(
                tool_pattern=payload.tool_pattern,
                agent_name=payload.agent_name,
                subagent_role=payload.subagent_role,
                sort_order=0,
                requires_provider=None,
                is_enabled=payload.is_enabled,
            )
        return ToolRead(**match)
