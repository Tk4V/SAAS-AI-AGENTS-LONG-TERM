"""Admin endpoints — global subagent catalog and the system-tool registry.

Three concentric Swagger groups:

* **Admin · Subagents** — CRUD over the global subagent catalog. Whatever
  you create here is immediately visible to every user via the public
  ``GET /subagents`` endpoint and may be attached to their agents.
* **Admin · System Tools** — read-only listing of the built-in SDK tools
  (Read, Edit, Bash variants, Agent). Patterns are seeded by migration
  ``0021`` and rarely change at runtime.
* **Admin · MCP Servers** — read-only listing of the MCP integrations the
  platform supports (github, jira, slack, aws, …).

Every route requires ``is_admin`` — either through the JWT claim of the
same name or through the ``ADMIN_USER_IDS`` allowlist in settings.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from src.api.dependencies import (
    AdminUserDep,
    MCPServerRepositoryDep,
    SubagentAdminRepositoryDep,
    SystemToolRepositoryDep,
)
from src.api.schemas.agent_schemas import (
    MCPServerRead,
    MCPServersList,
    SubagentAdminCreate,
    SubagentAdminUpdate,
    SubagentDetail,
    SystemToolRead,
    SystemToolsList,
)

router = APIRouter(prefix="/admin")


# ── Admin · System Tools ─────────────────────────────────────────────────────


class SystemToolsAdminView:
    """Built-in SDK tools admins can attach to subagents."""

    @staticmethod
    @router.get(
        "/system-tools",
        response_model=SystemToolsList,
        tags=["Admin · System Tools"],
        summary="List built-in SDK tools",
        description=(
            "Returns every active system tool (Read, Edit, Bash variants, "
            "Glob, Grep, Agent) with the SDK pattern admins use to attach "
            "the tool to a subagent via `POST /admin/subagents`."
        ),
    )
    async def list_system_tools(
        user: AdminUserDep,
        repo: SystemToolRepositoryDep,
    ) -> SystemToolsList:
        tools = await repo.list_active()
        return SystemToolsList(items=[SystemToolRead.from_orm(t) for t in tools])


# ── Admin · MCP Servers ──────────────────────────────────────────────────────


class MCPServersAdminView:
    """MCP integrations available platform-wide."""

    @staticmethod
    @router.get(
        "/mcp-servers",
        response_model=MCPServersList,
        tags=["Admin · MCP Servers"],
        summary="List MCP integrations",
        description=(
            "Returns active MCP server configs (github, jira, slack, aws, …). "
            "Use the returned `id` as `mcp_id` when calling either "
            "`POST /admin/subagents` (`mcp_server_config_ids`) or "
            "`POST /agents/{agent_id}/subagents/{subagent_id}/mcps/{mcp_id}`."
        ),
    )
    async def list_mcps(
        user: AdminUserDep,
        repo: MCPServerRepositoryDep,
    ) -> MCPServersList:
        configs = await repo.list_active()
        return MCPServersList(items=[MCPServerRead.from_orm(c) for c in configs])


# ── Admin · Subagents ────────────────────────────────────────────────────────


class SubagentsAdminView:
    """Create, edit, and remove the subagents users can pick from."""

    @staticmethod
    @router.get(
        "/subagents",
        response_model=list[SubagentDetail],
        tags=["Admin · Subagents"],
        summary="List subagents (admin view)",
        description=(
            "Returns every subagent in the catalog, **including inactive "
            "rows**. The response shape carries the full `system_prompt`, "
            "`model`, and the linked `system_tools` — fields the public "
            "`/subagents` endpoint does not expose."
        ),
    )
    async def list(
        user: AdminUserDep,
        repo: SubagentAdminRepositoryDep,
    ) -> list[SubagentDetail]:
        items = await repo.list_all()
        return [SubagentDetail.from_orm(s) for s in items]

    @staticmethod
    @router.post(
        "/subagents",
        response_model=SubagentDetail,
        status_code=status.HTTP_201_CREATED,
        tags=["Admin · Subagents"],
        summary="Create a new subagent",
        description=(
            "Adds a new entry to the global subagent catalog. Once created, "
            "users can attach it to their agents.\n\n"
            "**Workflow:**\n"
            "1. Pick the system tools the subagent needs from "
            "`GET /admin/system-tools` and put their ids in `system_tool_ids`.\n"
            "2. Pick the default MCP integrations from "
            "`GET /admin/mcp-servers` and put their ids in "
            "`mcp_server_config_ids` — these are copied into every user "
            "agent that later attaches this subagent.\n"
            "3. Give the subagent a unique slug (`name`), a human "
            "`display_name`, a description that explains *when* to delegate "
            "to it, and the `system_prompt` it should run with."
        ),
    )
    async def create(
        payload: SubagentAdminCreate,
        user: AdminUserDep,
        repo: SubagentAdminRepositoryDep,
    ) -> SubagentDetail:
        sub = await repo.create(
            name=payload.name,
            display_name=payload.display_name,
            description=payload.description,
            system_prompt=payload.system_prompt,
            model=payload.model,
            sort_order=payload.sort_order,
            is_active=payload.is_active,
        )
        if payload.system_tool_ids:
            await repo.set_system_tools(
                subagent_id=sub.id, system_tool_ids=payload.system_tool_ids,
            )
        if payload.mcp_server_config_ids:
            await repo.set_mcp_defaults(
                subagent_id=sub.id, mcp_server_config_ids=payload.mcp_server_config_ids,
            )
        sub = await repo.get(sub.id)
        return SubagentDetail.from_orm(sub)

    @staticmethod
    @router.get(
        "/subagents/{subagent_id}",
        response_model=SubagentDetail,
        tags=["Admin · Subagents"],
        summary="Get a subagent (admin view)",
        description="Returns the full admin view of one subagent — system prompt, system tools, MCP defaults.",
    )
    async def get(
        subagent_id: UUID,
        user: AdminUserDep,
        repo: SubagentAdminRepositoryDep,
    ) -> SubagentDetail:
        sub = await repo.get(subagent_id)
        return SubagentDetail.from_orm(sub)

    @staticmethod
    @router.patch(
        "/subagents/{subagent_id}",
        response_model=SubagentDetail,
        tags=["Admin · Subagents"],
        summary="Edit a subagent",
        description=(
            "Updates any subagent attribute. Passing `system_tool_ids` or "
            "`mcp_server_config_ids` **replaces** the entire set — pass the "
            "complete desired list, not just additions.\n\n"
            "Setting `is_active=false` soft-disables the subagent: it stays "
            "in `agent_subagents` rows so user agent shapes do not "
            "disappear, but the runtime silently skips it during "
            "delegation. Re-enable any time."
        ),
    )
    async def update(
        subagent_id: UUID,
        payload: SubagentAdminUpdate,
        user: AdminUserDep,
        repo: SubagentAdminRepositoryDep,
    ) -> SubagentDetail:
        sub = await repo.get(subagent_id)
        sub = await repo.update(
            subagent=sub,
            display_name=payload.display_name,
            description=payload.description,
            system_prompt=payload.system_prompt,
            model=payload.model,
            sort_order=payload.sort_order,
            is_active=payload.is_active,
        )
        if payload.system_tool_ids is not None:
            await repo.set_system_tools(
                subagent_id=sub.id, system_tool_ids=payload.system_tool_ids,
            )
        if payload.mcp_server_config_ids is not None:
            await repo.set_mcp_defaults(
                subagent_id=sub.id,
                mcp_server_config_ids=payload.mcp_server_config_ids,
            )
        sub = await repo.get(subagent_id)
        return SubagentDetail.from_orm(sub)

    @staticmethod
    @router.delete(
        "/subagents/{subagent_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["Admin · Subagents"],
        summary="Delete a subagent",
        description=(
            "Hard-deletes a subagent. Any `agent_subagents` rows that "
            "referenced it are removed via `ON DELETE CASCADE`, which "
            "means user agents that linked the subagent silently lose it. "
            "Prefer soft-disabling (`PATCH … {is_active: false}`) unless "
            "you really need to nuke the row."
        ),
    )
    async def delete(
        subagent_id: UUID,
        user: AdminUserDep,
        repo: SubagentAdminRepositoryDep,
    ) -> None:
        sub = await repo.get(subagent_id)
        await repo.delete(subagent=sub)
