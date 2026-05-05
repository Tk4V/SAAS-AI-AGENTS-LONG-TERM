"""User-facing tools endpoint.

Lists all active MCP integrations from the system catalog, enriched with
display metadata (display_name, category) from the provider config registry.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.api.dependencies import AgentConfigRepositoryDep, ProviderCatalogDep
from src.api.schemas.agent_schemas import ToolRead, ToolsList

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolsView:
    """Read available MCP tools."""

    @staticmethod
    @router.get("", response_model=ToolsList)
    async def list(
        repo: AgentConfigRepositoryDep,
        catalog: ProviderCatalogDep,
    ) -> ToolsList:
        """List all active MCP integrations with their display metadata.

        Each entry corresponds to one active MCP server. ``display_name`` and
        ``category`` come from the integration config in ``src/integrations/``.
        """
        mcp_configs = await repo.list_active_mcp_configs()

        provider_meta = {
            p.kind.value: (p.display_name, p.category.value)
            for p in catalog.all()
        }

        items = []
        for sort_order, config in enumerate(mcp_configs, start=1):
            display_name, category = provider_meta.get(config.provider_name, (config.provider_name, ""))
            items.append(ToolRead(
                tool_name=f"mcp__{config.provider_name}__*",
                sort_order=sort_order,
                display_name=display_name,
                category=category,
            ))
        return ToolsList(items=items)
