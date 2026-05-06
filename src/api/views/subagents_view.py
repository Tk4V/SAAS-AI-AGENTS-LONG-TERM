"""Public subagent catalog.

Read-only: lists what subagents an end-user can attach to their agents.
Mutating system-wide subagent state lives under ``/admin/subagents`` —
this module is intentionally idempotent.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.api.dependencies import AgentConfigRepositoryDep, ProviderCatalogDep
from src.api.schemas.agent_schemas import SubagentRead, SubagentToolRead, SubagentsList
from src.db.models.agent_config import Subagent

router = APIRouter(prefix="/subagents", tags=["Subagent Catalog"])


def _build_subagent_read(
    subagent: Subagent, provider_meta: dict[str, tuple[str, str]]
) -> SubagentRead:
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
        id=subagent.id,
        name=subagent.name,
        display_name=subagent.display_name,
        description=subagent.description,
        system_prompt=subagent.system_prompt,
        model=subagent.model,
        sort_order=subagent.sort_order,
        is_active=subagent.is_active,
        tools=tools,
    )


class SubagentCatalogView:
    """Read-only catalog of subagents available to attach to user agents."""

    @staticmethod
    @router.get(
        "",
        response_model=SubagentsList,
        summary="List all subagents available to pick from",
        description=(
            "Returns the full catalog of subagents that any user can attach "
            "to one of their agents. Use the returned `id` field as "
            "`subagent_id` when calling `POST /agents` (in `subagent_ids`) "
            "or `POST /agents/{agent_id}/subagents/{subagent_id}`.\n\n"
            "`tools` shows the default MCP integrations a subagent ships "
            "with — those defaults are copied into your agent at attach time."
        ),
    )
    async def list(
        repo: AgentConfigRepositoryDep,
        catalog: ProviderCatalogDep,
    ) -> SubagentsList:
        subagents = await repo.list_subagents()
        provider_meta = {
            p.kind.value: (p.display_name, p.category.value)
            for p in catalog.all()
        }
        return SubagentsList(
            items=[_build_subagent_read(s, provider_meta) for s in subagents]
        )
