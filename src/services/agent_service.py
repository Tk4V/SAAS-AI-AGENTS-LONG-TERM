"""Business rules for per-user agents.

The repository is the only thing that touches the DB. This service enforces
the user-facing invariants:

* an agent is created together with its first set of subagents (the "subagent
  cannot live without an orchestrator" rule),
* every newly attached subagent inherits the admin's MCP defaults from
  ``subagent_tools`` so the user has a working starting point,
* there is exactly one default agent per user, and creating the very first
  agent automatically marks it as the default.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from src.db.models.agent import Agent, AgentSubagent
from src.db.queries.agent_query import (
    AgentRepository,
    MCPServerRepository,
    SubagentAdminRepository,
)
from src.utils.exceptions import AlreadyExistsError, NotFoundError, ValidationError


class AgentService:
    """Manages user-owned orchestrator agents and their links."""

    def __init__(
        self,
        *,
        repository: AgentRepository,
        subagent_admin_repository: SubagentAdminRepository,
        mcp_repository: MCPServerRepository,
    ) -> None:
        self._repo = repository
        self._subagent_repo = subagent_admin_repository
        self._mcp_repo = mcp_repository
        self._logger = structlog.get_logger("clyde.service.agent")

    # ── User-facing operations ───────────────────────────────────────────────
    async def list_for_user(self, *, user_id: int) -> list[Agent]:
        return await self._repo.list_for_user(user_id=user_id)

    async def get(self, *, user_id: int, agent_id: UUID) -> Agent:
        return await self._repo.get(user_id=user_id, agent_id=agent_id)

    async def create(
        self,
        *,
        user_id: int,
        name: str,
        display_name: str,
        subagent_ids: list[UUID],
        description: str | None = None,
        is_default: bool = False,
    ) -> Agent:
        """Create a new agent and link the requested subagents in one transaction.

        ``subagent_ids`` must be non-empty: an orchestrator without subagents
        is meaningless in this product (it has nothing to delegate to).
        Each linked subagent has its admin MCP defaults copied into
        ``agent_subagent_mcps`` so the user starts with a working setup.
        """
        if not subagent_ids:
            raise ValidationError(
                "An agent must include at least one subagent.",
                details={"field": "subagent_ids"},
            )

        # Catch duplicates before hitting the DB.
        duplicates = {sid for sid in subagent_ids if subagent_ids.count(sid) > 1}
        if duplicates:
            raise ValidationError(
                "subagent_ids contains duplicate entries.",
                details={"duplicates": [str(s) for s in duplicates]},
            )

        # One round trip: load every requested subagent, then report missing
        # and inactive ones in one shot so the client can fix everything at
        # once instead of round-tripping per id.
        loaded = await self._subagent_repo.get_many(subagent_ids)
        loaded_by_id = {s.id: s for s in loaded}
        missing = [sid for sid in subagent_ids if sid not in loaded_by_id]
        if missing:
            raise ValidationError(
                "One or more subagent_ids do not exist.",
                details={"missing_subagent_ids": [str(s) for s in missing]},
            )
        inactive = [s.name for s in loaded if not s.is_active]
        if inactive:
            raise ValidationError(
                "One or more subagents are inactive and cannot be attached.",
                details={"inactive_subagent_names": inactive},
            )

        # First-agent-by-default: if the user has none yet, this one
        # becomes the default regardless of what was requested.
        existing = await self._repo.list_for_user(user_id=user_id)
        if not existing:
            is_default = True

        if is_default:
            # Clear any previous defaults before inserting the new one so the
            # partial-unique index does not bounce us.
            for other in existing:
                if other.is_default:
                    other.is_default = False
            # flush handled by repo on next write

        agent = await self._repo.create(
            user_id=user_id,
            name=name,
            display_name=display_name,
            description=description,
            is_default=is_default,
        )

        for index, subagent_id in enumerate(subagent_ids):
            link = await self._repo.link_subagent(
                agent_id=agent.id,
                subagent_id=subagent_id,
                sort_order=index,
            )
            await self._repo.copy_default_mcps_for_link(
                agent_subagent_id=link.id,
                subagent_id=subagent_id,
            )

        self._logger.info(
            "agent.created",
            agent_id=str(agent.id),
            user_id=user_id,
            subagent_count=len(subagent_ids),
        )
        return await self._repo.get(user_id=user_id, agent_id=agent.id)

    async def update(
        self,
        *,
        user_id: int,
        agent_id: UUID,
        name: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        is_default: bool | None = None,
    ) -> Agent:
        agent = await self._repo.get(user_id=user_id, agent_id=agent_id)
        if is_default is True:
            await self._repo.set_default(user_id=user_id, agent_id=agent_id)
        return await self._repo.update(
            agent=agent,
            name=name,
            display_name=display_name,
            description=description,
        )

    async def delete(self, *, user_id: int, agent_id: UUID) -> None:
        agent = await self._repo.get(user_id=user_id, agent_id=agent_id)
        await self._repo.delete(agent=agent)
        self._logger.info("agent.deleted", agent_id=str(agent_id), user_id=user_id)

    # ── Subagent linking ─────────────────────────────────────────────────────
    async def attach_subagent(
        self, *, user_id: int, agent_id: UUID, subagent_id: UUID
    ) -> AgentSubagent:
        agent = await self._repo.get(user_id=user_id, agent_id=agent_id)
        sub = await self._subagent_repo.get(subagent_id)
        if not sub.is_active:
            raise ValidationError(
                f"Subagent {sub.name!r} is inactive.",
            )
        link = await self._repo.link_subagent(
            agent_id=agent.id,
            subagent_id=subagent_id,
        )
        await self._repo.copy_default_mcps_for_link(
            agent_subagent_id=link.id,
            subagent_id=subagent_id,
        )
        # Reload with eager-loaded relations for the response.
        loaded = await self._repo.get_link(
            agent_id=agent.id, subagent_id=subagent_id
        )
        if loaded is None:
            raise NotFoundError("Failed to reload the new link.")
        return loaded

    async def detach_subagent(
        self, *, user_id: int, agent_id: UUID, subagent_id: UUID
    ) -> None:
        await self._repo.get(user_id=user_id, agent_id=agent_id)  # ownership
        await self._repo.unlink_subagent(
            agent_id=agent_id, subagent_id=subagent_id,
        )

    # ── Per-link MCP overrides ───────────────────────────────────────────────
    async def add_mcp(
        self,
        *,
        user_id: int,
        agent_id: UUID,
        subagent_id: UUID,
        mcp_id: UUID,
    ) -> None:
        await self._repo.get(user_id=user_id, agent_id=agent_id)  # ownership
        link = await self._repo.get_link(agent_id=agent_id, subagent_id=subagent_id)
        if link is None:
            raise NotFoundError(
                f"Subagent {subagent_id} is not attached to agent {agent_id}.",
            )
        await self._mcp_repo.get(mcp_id)  # validate the MCP exists / active
        await self._repo.add_mcp_to_link(
            agent_subagent_id=link.id,
            mcp_server_config_id=mcp_id,
        )

    async def remove_mcp(
        self,
        *,
        user_id: int,
        agent_id: UUID,
        subagent_id: UUID,
        mcp_id: UUID,
    ) -> None:
        await self._repo.get(user_id=user_id, agent_id=agent_id)
        link = await self._repo.get_link(agent_id=agent_id, subagent_id=subagent_id)
        if link is None:
            raise NotFoundError(
                f"Subagent {subagent_id} is not attached to agent {agent_id}.",
            )
        await self._repo.remove_mcp_from_link(
            agent_subagent_id=link.id,
            mcp_server_config_id=mcp_id,
        )

    # ── Pipeline support ─────────────────────────────────────────────────────
    async def resolve_agent_for_task(
        self, *, user_id: int, agent_id: UUID | None
    ) -> Agent:
        """Return the agent the runtime should use for a new task.

        Falls back to the user's default if ``agent_id`` is omitted. If the
        user has no agent at all yet, lazily provisions one bundling every
        currently active subagent — so first-time users can submit a task
        without a separate "create agent" step.
        """
        if agent_id is not None:
            return await self._repo.get(user_id=user_id, agent_id=agent_id)
        default = await self._repo.get_default(user_id=user_id)
        if default is not None:
            return default
        return await self._auto_provision_default(user_id=user_id)

    async def _auto_provision_default(self, *, user_id: int) -> Agent:
        """Create a ``default-orchestrator`` for a user that has no agent yet.

        Race-safe: two concurrent first-time POSTs from the same user will
        both call this method; the partial unique index on ``is_default``
        and the ``UNIQUE(user_id, name)`` constraint cause one of them to
        bounce with ``AlreadyExistsError``. We catch it and re-load the
        winner so both requests get a working agent instead of a 409.
        """
        active_subs = await self._subagent_repo.list_active()
        if not active_subs:
            raise ValidationError(
                "No active subagents are available; ask an administrator "
                "to seed the catalog before submitting a task.",
            )
        try:
            agent = await self._repo.create(
                user_id=user_id,
                name="default-orchestrator",
                display_name="Default Orchestrator",
                description="Auto-created on first task submission. Bundles every available subagent.",
                is_default=True,
            )
        except AlreadyExistsError:
            existing = await self._repo.get_default(user_id=user_id)
            if existing is not None:
                return existing
            # Concurrent caller created the row but with is_default=false
            # (or another agent name took the default slot). Fall through
            # by raising — the second request will retry on the next call.
            raise
        for index, sub in enumerate(active_subs):
            link = await self._repo.link_subagent(
                agent_id=agent.id, subagent_id=sub.id, sort_order=index,
            )
            await self._repo.copy_default_mcps_for_link(
                agent_subagent_id=link.id, subagent_id=sub.id,
            )
        self._logger.info(
            "agent.auto_provisioned",
            agent_id=str(agent.id),
            user_id=user_id,
            subagent_count=len(active_subs),
        )
        return await self._repo.get(user_id=user_id, agent_id=agent.id)
