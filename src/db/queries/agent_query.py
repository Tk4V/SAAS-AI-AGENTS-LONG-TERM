"""Database access for ``Agent`` and the supporting catalog tables.

Per-user orchestrator instances live in ``agents``; the link to subagents
goes through ``agent_subagents`` and the per-link MCP override lives in
``agent_subagent_mcps``. Admin-managed catalogs (``system_tools``,
``subagent_system_tools``) are read here as well so the runtime has a
single repository to consult.

The repository accepts an ``AsyncSession`` and uses ``flush`` rather than
``commit``: the FastAPI dependency wraps every request in a session that
commits on success and rolls back on error.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models.agent import (
    Agent,
    AgentSubagent,
    AgentSubagentMcp,
    SubagentSystemTool,
    SystemTool,
)
from src.db.models.agent_config import MCPServerConfig, Subagent, SubagentTool
from src.db.models.task import Task
from src.utils.exceptions import AlreadyExistsError, NotFoundError, ValidationError


class AgentRepository:
    """SQL access for per-user orchestrator agents and their links."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Reads ────────────────────────────────────────────────────────────────
    async def list_for_user(self, *, user_id: int) -> list[Agent]:
        stmt = (
            select(Agent)
            .where(Agent.user_id == user_id, Agent.is_active.is_(True))
            .order_by(Agent.is_default.desc(), Agent.created_at.asc())
        )
        return list((await self._session.execute(stmt)).scalars().unique().all())

    async def get(self, *, user_id: int, agent_id: UUID) -> Agent:
        stmt = (
            select(Agent)
            .where(Agent.id == agent_id, Agent.user_id == user_id)
            .options(
                selectinload(Agent.subagents)
                .selectinload(AgentSubagent.subagent),
                selectinload(Agent.subagents)
                .selectinload(AgentSubagent.mcps)
                .selectinload(AgentSubagentMcp.mcp_server),
            )
        )
        agent = (await self._session.execute(stmt)).scalar_one_or_none()
        if agent is None:
            raise NotFoundError(f"Agent {agent_id} was not found.")
        return agent

    async def get_default(self, *, user_id: int) -> Agent | None:
        stmt = (
            select(Agent)
            .where(
                Agent.user_id == user_id,
                Agent.is_default.is_(True),
                Agent.is_active.is_(True),
            )
            .options(
                selectinload(Agent.subagents)
                .selectinload(AgentSubagent.subagent),
                selectinload(Agent.subagents)
                .selectinload(AgentSubagent.mcps)
                .selectinload(AgentSubagentMcp.mcp_server),
            )
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_link(
        self, *, agent_id: UUID, subagent_id: UUID
    ) -> AgentSubagent | None:
        stmt = (
            select(AgentSubagent)
            .where(
                AgentSubagent.agent_id == agent_id,
                AgentSubagent.subagent_id == subagent_id,
            )
            .options(
                selectinload(AgentSubagent.subagent),
                selectinload(AgentSubagent.mcps).selectinload(AgentSubagentMcp.mcp_server),
            )
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # ── Writes — Agent ───────────────────────────────────────────────────────
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        display_name: str,
        description: str | None,
        system_prompt: str | None = None,
        model: str | None = None,
        is_default: bool = False,
    ) -> Agent:
        agent = Agent(
            user_id=user_id,
            name=name,
            display_name=display_name,
            description=description,
            system_prompt=system_prompt,
            model=model,
            is_default=is_default,
            is_active=True,
        )
        self._session.add(agent)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AlreadyExistsError(
                f"An agent named {name!r} already exists for this user.",
            ) from exc
        return agent

    async def update(
        self,
        *,
        agent: Agent,
        name: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
        is_active: bool | None = None,
    ) -> Agent:
        if name is not None:
            agent.name = name
        if display_name is not None:
            agent.display_name = display_name
        if description is not None:
            agent.description = description
        if system_prompt is not None:
            agent.system_prompt = system_prompt
        if model is not None:
            agent.model = model
        if is_active is not None:
            agent.is_active = is_active
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AlreadyExistsError(
                "Another agent with that name already exists.",
            ) from exc
        return agent

    async def count_tasks(self, *, agent_id: UUID) -> int:
        """Return how many tasks reference this agent — used before delete."""
        return await self._session.scalar(
            select(func.count(Task.id)).where(Task.agent_id == agent_id)
        ) or 0

    async def delete(self, *, agent: Agent) -> None:
        # Caller already verified ownership via .get(). We do an explicit
        # task-count check here so the user gets a precise number in the
        # error rather than a generic FK-violation message; the FK with
        # ON DELETE RESTRICT is the safety net if a race slips a task in
        # between the check and the delete.
        task_count = await self.count_tasks(agent_id=agent.id)
        if task_count > 0:
            raise ValidationError(
                f"Agent has {task_count} task(s) referencing it. Delete or "
                f"reassign those tasks before removing the agent.",
                details={"agent_id": str(agent.id), "task_count": task_count},
            )
        try:
            await self._session.delete(agent)
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValidationError(
                "Agent could not be deleted because it is still referenced.",
                details={"agent_id": str(agent.id)},
            ) from exc

    async def set_default(self, *, user_id: int, agent_id: UUID) -> Agent:
        # Postgres enforces "at most one default per user" via the partial
        # unique index, but the index forbids both rows being default at
        # once even momentarily. We therefore clear all defaults first and
        # set the new one in the same transaction.
        clear_stmt = (
            select(Agent)
            .where(Agent.user_id == user_id, Agent.is_default.is_(True))
        )
        for existing in (await self._session.execute(clear_stmt)).scalars():
            existing.is_default = False
        await self._session.flush()

        target = await self.get(user_id=user_id, agent_id=agent_id)
        target.is_default = True
        await self._session.flush()
        return target

    # ── Writes — links and MCPs ──────────────────────────────────────────────
    async def link_subagent(
        self,
        *,
        agent_id: UUID,
        subagent_id: UUID,
        sort_order: int = 0,
    ) -> AgentSubagent:
        link = AgentSubagent(
            agent_id=agent_id,
            subagent_id=subagent_id,
            sort_order=sort_order,
            is_active=True,
        )
        self._session.add(link)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AlreadyExistsError(
                "Subagent is already linked to this agent.",
            ) from exc
        return link

    async def unlink_subagent(
        self, *, agent_id: UUID, subagent_id: UUID
    ) -> None:
        await self._session.execute(
            delete(AgentSubagent).where(
                AgentSubagent.agent_id == agent_id,
                AgentSubagent.subagent_id == subagent_id,
            )
        )
        await self._session.flush()

    async def add_mcp_to_link(
        self, *, agent_subagent_id: UUID, mcp_server_config_id: UUID
    ) -> AgentSubagentMcp:
        link_mcp = AgentSubagentMcp(
            agent_subagent_id=agent_subagent_id,
            mcp_server_config_id=mcp_server_config_id,
            is_active=True,
        )
        self._session.add(link_mcp)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AlreadyExistsError(
                "MCP is already attached to this subagent inside this agent.",
            ) from exc
        return link_mcp

    async def remove_mcp_from_link(
        self, *, agent_subagent_id: UUID, mcp_server_config_id: UUID
    ) -> None:
        await self._session.execute(
            delete(AgentSubagentMcp).where(
                AgentSubagentMcp.agent_subagent_id == agent_subagent_id,
                AgentSubagentMcp.mcp_server_config_id == mcp_server_config_id,
            )
        )
        await self._session.flush()

    async def copy_default_mcps_for_link(
        self, *, agent_subagent_id: UUID, subagent_id: UUID
    ) -> None:
        """Seed ``agent_subagent_mcps`` from the admin defaults in ``subagent_tools``.

        Called right after ``link_subagent`` so the user's new link starts
        with the MCP set the admin configured for that subagent.
        """
        defaults_stmt = select(SubagentTool).where(
            SubagentTool.subagent_id == subagent_id
        )
        for default in (await self._session.execute(defaults_stmt)).scalars():
            self._session.add(
                AgentSubagentMcp(
                    agent_subagent_id=agent_subagent_id,
                    mcp_server_config_id=default.mcp_server_config_id,
                    is_active=default.is_active,
                )
            )
        await self._session.flush()

    # ── Reads — runtime helpers ──────────────────────────────────────────────
    async def list_subagents_for_agent(
        self, *, agent_id: UUID, only_active: bool = True
    ) -> list[AgentSubagent]:
        """Return the subagent links of an agent, eager-loaded for runtime use.

        When ``only_active`` is true (the runtime path), the query also
        joins ``Subagent`` and filters out subagents the admin has
        soft-disabled — old user agents that still link to a now-disabled
        subagent will silently skip it instead of crashing the SDK session.
        """
        stmt = (
            select(AgentSubagent)
            .where(AgentSubagent.agent_id == agent_id)
            .options(
                selectinload(AgentSubagent.subagent).selectinload(Subagent.system_tools).selectinload(SubagentSystemTool.system_tool),
                selectinload(AgentSubagent.mcps).selectinload(AgentSubagentMcp.mcp_server),
            )
            .order_by(AgentSubagent.sort_order)
        )
        if only_active:
            stmt = (
                stmt
                .join(Subagent, Subagent.id == AgentSubagent.subagent_id)
                .where(
                    AgentSubagent.is_active.is_(True),
                    Subagent.is_active.is_(True),
                )
            )
        return list((await self._session.execute(stmt)).scalars().unique().all())


class SystemToolRepository:
    """Read-only access to the admin catalog of built-in SDK tools."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(self) -> list[SystemTool]:
        stmt = (
            select(SystemTool)
            .where(SystemTool.is_active.is_(True))
            .order_by(SystemTool.sort_order, SystemTool.name)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_by_name(self, name: str) -> SystemTool | None:
        stmt = select(SystemTool).where(SystemTool.name == name)
        return (await self._session.execute(stmt)).scalar_one_or_none()


class SubagentAdminRepository:
    """Admin CRUD for the ``subagents`` table and its system-tool links.

    Sits next to ``AgentRepository`` because the admin endpoints that
    operate on subagents need write access (``AgentConfigRepository`` only
    exposes reads / MCP toggles).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[Subagent]:
        stmt = select(Subagent).order_by(Subagent.sort_order, Subagent.name)
        return list((await self._session.execute(stmt)).scalars().unique().all())

    async def list_active(self) -> list[Subagent]:
        stmt = (
            select(Subagent)
            .where(Subagent.is_active.is_(True))
            .order_by(Subagent.sort_order, Subagent.name)
        )
        return list((await self._session.execute(stmt)).scalars().unique().all())

    async def get(self, subagent_id: UUID) -> Subagent:
        stmt = select(Subagent).where(Subagent.id == subagent_id)
        subagent = (await self._session.execute(stmt)).scalar_one_or_none()
        if subagent is None:
            raise NotFoundError(f"Subagent {subagent_id} was not found.")
        return subagent

    async def get_by_name(self, name: str) -> Subagent | None:
        stmt = select(Subagent).where(Subagent.name == name)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_many(self, subagent_ids: list[UUID]) -> list[Subagent]:
        """Fetch many subagents by id. Order is not guaranteed."""
        if not subagent_ids:
            return []
        stmt = select(Subagent).where(Subagent.id.in_(subagent_ids))
        return list((await self._session.execute(stmt)).scalars().all())

    async def create(
        self,
        *,
        name: str,
        display_name: str,
        description: str,
        system_prompt: str,
        model: str,
        sort_order: int = 0,
        is_active: bool = True,
    ) -> Subagent:
        subagent = Subagent(
            name=name,
            display_name=display_name,
            description=description,
            system_prompt=system_prompt,
            model=model,
            sort_order=sort_order,
            is_active=is_active,
        )
        self._session.add(subagent)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AlreadyExistsError(
                f"Subagent {name!r} already exists.",
            ) from exc
        return subagent

    async def update(
        self,
        *,
        subagent: Subagent,
        display_name: str | None = None,
        description: str | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
        sort_order: int | None = None,
        is_active: bool | None = None,
    ) -> Subagent:
        if display_name is not None:
            subagent.display_name = display_name
        if description is not None:
            subagent.description = description
        if system_prompt is not None:
            subagent.system_prompt = system_prompt
        if model is not None:
            subagent.model = model
        if sort_order is not None:
            subagent.sort_order = sort_order
        if is_active is not None:
            subagent.is_active = is_active
        await self._session.flush()
        return subagent

    async def delete(self, *, subagent: Subagent) -> None:
        # CASCADE on agent_subagents removes any user links automatically.
        await self._session.delete(subagent)
        await self._session.flush()

    async def set_system_tools(
        self, *, subagent_id: UUID, system_tool_ids: list[UUID]
    ) -> list[SubagentSystemTool]:
        """Replace the subagent's system-tool set with the provided list."""
        await self._session.execute(
            delete(SubagentSystemTool).where(
                SubagentSystemTool.subagent_id == subagent_id,
            )
        )
        rows = [
            SubagentSystemTool(
                subagent_id=subagent_id,
                system_tool_id=tid,
                is_active=True,
            )
            for tid in system_tool_ids
        ]
        self._session.add_all(rows)
        await self._session.flush()
        return rows

    async def set_mcp_defaults(
        self, *, subagent_id: UUID, mcp_server_config_ids: list[UUID]
    ) -> list[SubagentTool]:
        """Replace the admin MCP-default set for the subagent."""
        await self._session.execute(
            delete(SubagentTool).where(SubagentTool.subagent_id == subagent_id)
        )
        rows = [
            SubagentTool(
                subagent_id=subagent_id,
                mcp_server_config_id=mid,
                is_active=True,
            )
            for mid in mcp_server_config_ids
        ]
        self._session.add_all(rows)
        await self._session.flush()
        return rows


class MCPServerRepository:
    """Read-only listing of admin-configured MCP server entries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(self) -> list[MCPServerConfig]:
        stmt = (
            select(MCPServerConfig)
            .where(MCPServerConfig.is_active.is_(True))
            .order_by(MCPServerConfig.provider_name)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get(self, mcp_id: UUID) -> MCPServerConfig:
        stmt = select(MCPServerConfig).where(MCPServerConfig.id == mcp_id)
        cfg = (await self._session.execute(stmt)).scalar_one_or_none()
        if cfg is None:
            raise NotFoundError(f"MCP server config {mcp_id} was not found.")
        return cfg
