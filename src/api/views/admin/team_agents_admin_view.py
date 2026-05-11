"""Admin endpoints — pipeline agent configs (orchestrator, publisher).

Rows are seeded by migration 0028 and are **update-only** — admins can edit
``system_prompt``, ``model``, ``prompt_template``, ``is_active``, and the
linked system tools, but cannot create or delete rows. Changes take effect
on the next task execution without a redeploy.

Every route requires ``is_admin``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from src.api.dependencies import AdminUserDep, SessionDep, SystemToolRepositoryDep
from src.api.schemas.agent_schemas import TeamAgentConfigResponse, TeamAgentConfigUpdate
from src.db.queries.agent_config_query import TeamAgentConfigRepository

router = APIRouter(prefix="/admin")


class TeamAgentsAdminView:
    """Update the system prompt, model, and tool list for orchestrator / publisher."""

    @staticmethod
    @router.get(
        "/team-agents",
        response_model=list[TeamAgentConfigResponse],
        tags=["Admin · Team Agents"],
        summary="List pipeline agent configs",
        description=(
            "Returns every pipeline agent config (orchestrator, publisher), "
            "**including inactive rows**, with the full ``system_prompt``, "
            "``model``, ``prompt_template``, and linked ``system_tools``."
        ),
    )
    async def list(
        user: AdminUserDep,
        session: SessionDep,
    ) -> list[TeamAgentConfigResponse]:
        repo = TeamAgentConfigRepository(session)
        items = await repo.list_all()
        return [TeamAgentConfigResponse.from_orm(c) for c in items]

    @staticmethod
    @router.get(
        "/team-agents/{name}",
        response_model=TeamAgentConfigResponse,
        tags=["Admin · Team Agents"],
        summary="Get a pipeline agent config",
        description="Returns the full admin view of one pipeline agent config by slug name.",
    )
    async def get(
        name: str,
        user: AdminUserDep,
        session: SessionDep,
    ) -> TeamAgentConfigResponse:
        repo = TeamAgentConfigRepository(session)
        # list_all includes inactive; look up by name without active filter
        from sqlalchemy import select
        from src.db.models.agent_config import TeamAgentConfig
        stmt = select(TeamAgentConfig).where(TeamAgentConfig.name == name)
        config = (await session.execute(stmt)).scalars().first()
        if config is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No pipeline agent config with name '{name}'.",
            )
        return TeamAgentConfigResponse.from_orm(config)

    @staticmethod
    @router.patch(
        "/team-agents/{name}",
        response_model=TeamAgentConfigResponse,
        tags=["Admin · Team Agents"],
        summary="Update a pipeline agent config",
        description=(
            "Updates any attribute of a pipeline agent config. Passing "
            "``system_tool_ids`` **replaces** the entire tool set — "
            "send the complete desired list, not just additions.\n\n"
            "Changes take effect immediately on the next task execution "
            "without a redeploy."
        ),
    )
    async def update(
        name: str,
        payload: TeamAgentConfigUpdate,
        user: AdminUserDep,
        session: SessionDep,
        system_tool_repo: SystemToolRepositoryDep,
    ) -> TeamAgentConfigResponse:
        from sqlalchemy import select
        from src.db.models.agent_config import TeamAgentConfig
        stmt = select(TeamAgentConfig).where(TeamAgentConfig.name == name)
        config = (await session.execute(stmt)).scalars().first()
        if config is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No pipeline agent config with name '{name}'.",
            )

        repo = TeamAgentConfigRepository(session)
        config = await repo.update(
            config=config,
            display_name=payload.display_name,
            system_prompt=payload.system_prompt,
            model=payload.model,
            prompt_template=payload.prompt_template,
            is_active=payload.is_active,
        )
        if payload.system_tool_ids is not None:
            await repo.set_system_tools(
                team_agent_config_id=config.id,
                system_tool_ids=payload.system_tool_ids,
            )

        # Reload to pick up fresh system_tools relationship
        await session.refresh(config)
        return TeamAgentConfigResponse.from_orm(config)
