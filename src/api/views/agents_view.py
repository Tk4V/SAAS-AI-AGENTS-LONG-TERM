"""HTTP views for per-user orchestrator agents.

Three concentric resources, exposed as three Swagger groups:

1. **Agents** — the orchestrator records the user owns.
2. **Agent Subagents** — which catalog subagents are attached to one agent.
3. **Agent MCPs** — which MCP integrations a specific (agent, subagent)
   link is allowed to use.

Every endpoint here is scoped to the JWT's ``user_id``. There is no
``/admin`` surface for these — the user is the only one who can look at
or change their own agents.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from src.api.dependencies import AgentServiceDep, CurrentUserDep
from src.api.schemas.agent_schemas import (
    AgentCreate,
    AgentListItem,
    AgentRead,
    AgentSubagentRead,
    AgentUpdate,
    AgentsList,
)

router = APIRouter(prefix="/agents")


# ── 1. Agents ────────────────────────────────────────────────────────────────


class AgentsView:
    """User-owned orchestrator agents (CRUD)."""

    @staticmethod
    @router.get(
        "",
        response_model=AgentsList,
        tags=["Agents"],
        summary="List my agents",
        description=(
            "Returns every orchestrator agent that belongs to the calling "
            "user, with the count of attached subagents per row. The first "
            "result is the user's default agent (if any)."
        ),
    )
    async def list(
        user: CurrentUserDep,
        service: AgentServiceDep,
    ) -> AgentsList:
        agents = await service.list_for_user(user_id=user.id)
        return AgentsList(items=[AgentListItem.from_orm(a) for a in agents])

    @staticmethod
    @router.post(
        "",
        response_model=AgentRead,
        status_code=status.HTTP_201_CREATED,
        tags=["Agents"],
        summary="Create a new agent",
        description=(
            "Creates a brand-new orchestrator agent owned by the caller and "
            "attaches the requested subagents in one transaction.\n\n"
            "**Workflow:**\n"
            "1. `GET /subagents` — pick the subagents you want.\n"
            "2. Send their `id` values in the `subagent_ids` array (must be "
            "non-empty — an orchestrator cannot exist without subagents).\n"
            "3. The first agent you create is automatically marked as the "
            "default; later ones inherit `is_default: false` unless you ask "
            "for the flag explicitly.\n\n"
            "Each attached subagent inherits the admin's MCP defaults — you "
            "can change them later via the **Agent MCPs** endpoints."
        ),
    )
    async def create(
        payload: AgentCreate,
        user: CurrentUserDep,
        service: AgentServiceDep,
    ) -> AgentRead:
        agent = await service.create(
            user_id=user.id,
            name=payload.name,
            display_name=payload.display_name,
            description=payload.description,
            subagent_ids=payload.subagent_ids,
            is_default=payload.is_default,
        )
        return AgentRead.from_orm(agent)

    @staticmethod
    @router.get(
        "/{agent_id}",
        response_model=AgentRead,
        tags=["Agents"],
        summary="Get one of my agents",
        description=(
            "Returns the full agent payload, including every attached "
            "subagent and the MCP integrations enabled per subagent."
        ),
    )
    async def get(
        agent_id: UUID,
        user: CurrentUserDep,
        service: AgentServiceDep,
    ) -> AgentRead:
        agent = await service.get(user_id=user.id, agent_id=agent_id)
        return AgentRead.from_orm(agent)

    @staticmethod
    @router.patch(
        "/{agent_id}",
        response_model=AgentRead,
        tags=["Agents"],
        summary="Edit my agent",
        description=(
            "Updates metadata of an agent: name, display name, description, "
            "or the default flag. Setting `is_default: true` clears the "
            "default flag from any other agent of the same user — Postgres "
            "enforces one default per user via a partial unique index."
        ),
    )
    async def update(
        agent_id: UUID,
        payload: AgentUpdate,
        user: CurrentUserDep,
        service: AgentServiceDep,
    ) -> AgentRead:
        await service.update(
            user_id=user.id,
            agent_id=agent_id,
            name=payload.name,
            display_name=payload.display_name,
            description=payload.description,
            is_default=payload.is_default,
        )
        agent = await service.get(user_id=user.id, agent_id=agent_id)
        return AgentRead.from_orm(agent)

    @staticmethod
    @router.delete(
        "/{agent_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["Agents"],
        summary="Delete my agent",
        description=(
            "Permanently removes an agent and every link to subagents and "
            "MCP overrides it owns. Fails with 422 if there are tasks still "
            "referencing the agent — the response includes the exact task "
            "count so the client can show a remediation hint."
        ),
    )
    async def delete(
        agent_id: UUID,
        user: CurrentUserDep,
        service: AgentServiceDep,
    ) -> None:
        await service.delete(user_id=user.id, agent_id=agent_id)


# ── 2. Agent Subagents ───────────────────────────────────────────────────────


class AgentSubagentsView:
    """Attach catalog subagents to one of my agents."""

    @staticmethod
    @router.post(
        "/{agent_id}/subagents/{subagent_id}",
        response_model=AgentSubagentRead,
        status_code=status.HTTP_201_CREATED,
        tags=["Agent Subagents"],
        summary="Attach a subagent to my agent",
        description=(
            "Links a subagent from the public catalog to one of my agents. "
            "MCP defaults configured by the admin for that subagent are "
            "automatically copied into the new link, so the subagent has a "
            "working set of integrations from the start.\n\n"
            "Returns the link with its inherited MCP set — the response "
            "shape matches what `GET /agents/{id}` shows for one entry of "
            "`subagents`."
        ),
    )
    async def attach(
        agent_id: UUID,
        subagent_id: UUID,
        user: CurrentUserDep,
        service: AgentServiceDep,
    ) -> AgentSubagentRead:
        link = await service.attach_subagent(
            user_id=user.id, agent_id=agent_id, subagent_id=subagent_id,
        )
        return AgentSubagentRead.from_orm(link)

    @staticmethod
    @router.delete(
        "/{agent_id}/subagents/{subagent_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["Agent Subagents"],
        summary="Detach a subagent from my agent",
        description=(
            "Removes the (agent, subagent) link and every MCP override that "
            "depended on it. The subagent itself remains in the global "
            "catalog and other agents are unaffected."
        ),
    )
    async def detach(
        agent_id: UUID,
        subagent_id: UUID,
        user: CurrentUserDep,
        service: AgentServiceDep,
    ) -> None:
        await service.detach_subagent(
            user_id=user.id, agent_id=agent_id, subagent_id=subagent_id,
        )


# ── 3. Agent MCPs (per subagent inside an agent) ─────────────────────────────


class AgentSubagentMcpsView:
    """Tweak which MCP integrations one subagent has inside one of my agents."""

    @staticmethod
    @router.post(
        "/{agent_id}/subagents/{subagent_id}/mcps/{mcp_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["Agent MCPs"],
        summary="Enable an MCP integration for this subagent inside this agent",
        description=(
            "Adds an MCP integration to the (agent, subagent) link. The "
            "`mcp_id` must come from `GET /admin/mcp-servers` — listing "
            "active MCPs is admin-gated because it exposes server "
            "configuration that end-users do not need to see directly."
        ),
    )
    async def add_mcp(
        agent_id: UUID,
        subagent_id: UUID,
        mcp_id: UUID,
        user: CurrentUserDep,
        service: AgentServiceDep,
    ) -> None:
        await service.add_mcp(
            user_id=user.id,
            agent_id=agent_id,
            subagent_id=subagent_id,
            mcp_id=mcp_id,
        )

    @staticmethod
    @router.delete(
        "/{agent_id}/subagents/{subagent_id}/mcps/{mcp_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["Agent MCPs"],
        summary="Disable an MCP integration for this subagent inside this agent",
        description=(
            "Removes the MCP override for this (agent, subagent) link. "
            "Admin-default MCPs that were never overridden are unaffected."
        ),
    )
    async def remove_mcp(
        agent_id: UUID,
        subagent_id: UUID,
        mcp_id: UUID,
        user: CurrentUserDep,
        service: AgentServiceDep,
    ) -> None:
        await service.remove_mcp(
            user_id=user.id,
            agent_id=agent_id,
            subagent_id=subagent_id,
            mcp_id=mcp_id,
        )
