"""Minimal contract every Clyde agent fulfills.

For agents that need to launch a Claude Agent SDK session, inherit from
``SDKAgent`` (in ``sdk_agent.py``) instead — it adds the SDK configuration
contract on top of this base.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

import structlog

from src.app_context import AppContext, app_context as default_app_context
from src.clients import Clients, clients as default_clients
from src.integrations._shared.token_resolver import TokenResolver


class BaseAgent(ABC):
    """Abstract base for every Clyde pipeline agent.

    To create a new agent:
        1. Set the class attribute ``name`` (short identifier used in logs and
           the pipeline runner — e.g. ``"publisher"``).
        2. Set the class attribute ``role`` (human-readable label — e.g.
           ``"Publisher"``).
        3. Implement ``async execute(self, state) -> dict``. Return only the
           keys the agent contributes to the pipeline state.

    The base provides for free:
        - ``self.ctx`` — `AppContext` (settings, cipher). Tests pass an
          ``AppContext`` instance via the constructor; production uses the
          global singleton by default.
        - ``self.clients`` — `Clients` (anthropic, http). Same DI pattern.
        - ``self.token_resolver`` — `TokenResolver` for fetching plaintext
          OAuth tokens out of the database.
        - ``self.logger`` — structlog logger scoped to the agent's ``name``.
        - ``__call__(state)`` — lifecycle wrapper that logs start/finish/fail
          around every ``execute`` invocation.
        - ``resolve_github_token(user_id)`` — convenience for the common
          "give me the GitHub access token for this user" path.

    Agents that need to drive an autonomous Claude Agent SDK loop should
    inherit from ``SDKAgent`` instead, which extends this contract with
    ``SDK_ALLOWED_TOOLS`` + ``build_mcp_servers`` + ``run_sdk_session``.
    """

    name: ClassVar[str]
    role: ClassVar[str]

    def __init__(
        self,
        *,
        app_context: AppContext | None = None,
        clients: Clients | None = None,
    ) -> None:
        self._ctx = app_context or default_app_context
        self._clients = clients or default_clients
        self._logger = structlog.get_logger(f"clyde.agent.{self.name}")
        self._token_resolver = TokenResolver(cipher=self._ctx.cipher)

    @property
    def ctx(self) -> AppContext:
        """Configuration-derived singletons (settings, cipher)."""
        return self._ctx

    @property
    def clients(self) -> Clients:
        """Network clients with connection pools (anthropic, http)."""
        return self._clients

    @property
    def token_resolver(self) -> TokenResolver:
        """DB-backed resolver returning plaintext OAuth tokens."""
        return self._token_resolver

    @property
    def logger(self) -> Any:
        """Bound structlog logger scoped to this agent's name."""
        return self._logger

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the agent, wrapping ``execute`` with start/finish lifecycle events."""
        self._logger.info(
            "agent.started",
            task_id=state.get("task_id"),
            attempt=state.get("attempt"),
        )
        try:
            result = await self.execute(state)
        except Exception as exc:
            self._logger.exception("agent.failed", error=str(exc))
            raise
        self._logger.info("agent.finished", produced_keys=list(result.keys()))
        return result

    @abstractmethod
    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Do the work and return only the keys the agent contributes to the state."""

    async def resolve_github_token(self, *, user_id: int) -> str:
        """Fetch and decrypt the user's GitHub OAuth token."""
        from src.integrations._shared.kinds import IntegrationKind
        return await self._token_resolver.resolve(
            user_id=user_id, kind=IntegrationKind.GITHUB
        )

    async def resolve_jira_token(self, *, user_id: int) -> tuple[str, str, str] | None:
        """Fetch the user's Jira token, site URL, and cloud ID.

        Returns ``(access_token, site_url, cloud_id)`` or ``None`` if the user
        has not connected Jira or if cloud metadata was not stored during the
        OAuth callback. Callers should treat ``None`` as "Jira not available"
        and skip Jira-specific behaviour gracefully.
        """
        return await self._token_resolver.resolve_jira(user_id=user_id)
