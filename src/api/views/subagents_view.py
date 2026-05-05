"""Subagents endpoint.

Lists all DB-configured subagents with their allowed MCP tools.
Each subagent's system tools (Read, Edit, Bash, etc.) remain hardcoded in the
agent layer and are not surfaced here — only MCP tool associations are shown.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from src.api.dependencies import AgentConfigRepositoryDep, ProviderCatalogDep
from src.api.schemas.agent_schemas import (
    SubagentRead,
    SubagentsList,
    SubagentToolRead,
    SubagentToolUpdate,
)
from src.db.models.agent_config import Subagent

router = APIRouter(prefix="/subagents", tags=["subagents"])


def _build_subagent_read(subagent: Subagent, provider_meta: dict[str, tuple[str, str]]) -> SubagentRead:
    tools = [
        SubagentToolRead(
            tool_name=f"mcp__{t.mcp_server.provider_name}__*",
            display_name=provider_meta.get(t.mcp_server.provider_name, (t.mcp_server.provider_name, ""))[0],
            category=provider_meta.get(t.mcp_server.provider_name, ("", ""))[1],
            is_active=t.is_active,
        )
        for t in subagent.tools
    ]
    return SubagentRead(
        name=subagent.name,
        display_name=subagent.display_name,
        description=subagent.description,
        system_prompt=subagent.system_prompt,
        model=subagent.model,
        sort_order=subagent.sort_order,
        is_active=subagent.is_active,
        tools=tools,
    )


class SubagentsView:
    """Read and manage subagent configurations."""

    @staticmethod
    @router.get("", response_model=SubagentsList)
    async def list(
        repo: AgentConfigRepositoryDep,
        catalog: ProviderCatalogDep,
    ) -> SubagentsList:
        """List all active subagents with their allowed MCP tools.

        ``tools`` contains MCP integrations only. System tools (Read, Edit, Bash, etc.)
        are hardcoded in the agent layer and not returned here.
        """
        subagents = await repo.list_subagents()
        provider_meta = {
            p.kind.value: (p.display_name, p.category.value)
            for p in catalog.all()
        }
        return SubagentsList(items=[_build_subagent_read(s, provider_meta) for s in subagents])

    @staticmethod
    @router.patch("/{name}", response_model=SubagentRead)
    async def update(
        name: str,
        payload: SubagentToolUpdate,
        repo: AgentConfigRepositoryDep,
        catalog: ProviderCatalogDep,
    ) -> SubagentRead:
        """Enable or disable an MCP tool for a subagent.

        Upserts a row in ``subagent_tools``. The change takes effect on the
        next orchestrator run — running sessions are not affected.
        """
        subagent = await repo.get_subagent_by_name(name)
        if subagent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Subagent {name!r} not found.")

        mcp_config = await repo.get_mcp_config_by_provider(payload.mcp_provider)
        if mcp_config is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"MCP provider {payload.mcp_provider!r} not found or inactive.",
            )

        await repo.upsert_subagent_tool(
            subagent_id=subagent.id,
            mcp_server_config_id=mcp_config.id,
            is_active=payload.is_active,
        )

        # Re-fetch to return up-to-date state
        subagent = await repo.get_subagent_by_name(name)
        provider_meta = {
            p.kind.value: (p.display_name, p.category.value)
            for p in catalog.all()
        }
        return _build_subagent_read(subagent, provider_meta)  # type: ignore[arg-type]
