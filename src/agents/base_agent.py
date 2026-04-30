"""Minimal contract every Clyde agent fulfills.

For agents that need to launch a Claude Agent SDK session, inherit from
``SDKAgent`` (in ``sdk_agent.py``) instead — it adds the SDK configuration
contract on top of this base.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

import structlog

from src.app_context import AppContext
from src.app_context import app_context as default_app_context
from src.clients import Clients
from src.clients import clients as default_clients
from src.credentials.audit import CredentialAuditor
from src.credentials.kinds.registry import get_kind_registry
from src.credentials.oauth.refresher import OAuthRefresher
from src.credentials.oauth.token_provider import OAuthTokenProvider
from src.credentials.payloads.oauth import OAuthMetadata
from src.credentials.resolver import CredentialResolver
from src.db.models.credential import CredentialKind
from src.db.models.project import ProviderKind
from src.db.queries.credential_event_query import CredentialEventRepository
from src.db.queries.credential_query import CredentialRepository
from src.db.session import db
from src.integrations._shared import (
    AuthlibClientFactory,
    OAuthAdapter,
    OAuthStateSigner,
    ProviderCatalog,
)


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
        - ``self.ctx`` — `AppContext` (settings, cipher).
        - ``self.clients`` — `Clients` (anthropic, http).
        - ``self.token_provider`` — `OAuthTokenProvider` returning plaintext
          OAuth access tokens out of the unified credentials table.
        - ``self.logger`` — structlog logger scoped to the agent's ``name``.
        - ``__call__(state)`` — lifecycle wrapper that logs start/finish/fail
          around every ``execute`` invocation.
        - ``resolve_github_token(user_id)`` — convenience for the common
          "give me the GitHub access token for this user" path.

    Agents that need to drive an autonomous Claude Agent SDK loop should
    inherit from ``SDKAgent`` instead.
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
        self._oauth_adapter = self._build_oauth_adapter()
        self._token_provider = OAuthTokenProvider(
            adapter=self._oauth_adapter,
            cipher=self._ctx.cipher,
        )

    @staticmethod
    def _build_oauth_adapter() -> OAuthAdapter:
        catalog = ProviderCatalog()
        factory = AuthlibClientFactory(catalog_lookup=catalog.get)
        signer = OAuthStateSigner()
        return OAuthAdapter(
            catalog_lookup=catalog.get,
            client_factory=factory,
            state_signer=signer,
        )

    @property
    def ctx(self) -> AppContext:
        """Configuration-derived singletons (settings, cipher)."""
        return self._ctx

    @property
    def clients(self) -> Clients:
        """Network clients with connection pools (anthropic, http)."""
        return self._clients

    @property
    def token_provider(self) -> OAuthTokenProvider:
        """OAuth token provider backed by the unified credentials store."""
        return self._token_provider

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
        """Fetch the user's GitHub OAuth access token."""
        return await self._token_provider.get_access_token(
            user_id=user_id, provider=ProviderKind.GITHUB
        )

    async def build_user_mcp_servers(self, *, user_id: int) -> dict[str, Any]:
        """Mount MCP servers for every OAuth integration the user has connected.

        Reads the user's active OAuth credentials from the unified
        ``credentials`` table, runs each one through ``CredentialResolver`` so
        expiring tokens are refreshed, and asks the matching provider config
        for an MCP factory. Providers without an ``mcp_factory`` are skipped;
        factories that raise are logged but never block the others.

        Returns a dict ready for ``ClaudeAgentOptions.mcp_servers`` keyed by
        ``ProviderKind.value`` (e.g. ``"github"``, ``"jira"``).
        """
        catalog = ProviderCatalog()
        kinds = get_kind_registry()
        cipher = self._ctx.cipher

        async with db.session_scope() as session:
            repo = CredentialRepository(session)
            events = CredentialEventRepository(session)
            auditor = CredentialAuditor(events=events)
            refresher = OAuthRefresher(
                repository=repo,
                adapter=self._oauth_adapter,
                cipher=cipher,
                handler=kinds.get(CredentialKind.OAUTH),
            )
            resolver = CredentialResolver(
                repository=repo,
                cipher=cipher,
                kinds=kinds,
                auditor=auditor,
                oauth_refresher=refresher,
            )
            credentials = await repo.list_active_oauth_for_user(user_id=user_id)

            servers: dict[str, Any] = {}
            for credential in credentials:
                metadata = OAuthMetadata(**credential.metadata_json)
                try:
                    provider = ProviderKind(metadata.provider)
                except ValueError:
                    continue
                cfg = catalog.get(provider) if provider in catalog else None
                if cfg is None or cfg.mcp_factory is None:
                    continue
                try:
                    resolved = await resolver.resolve(
                        user_id=user_id,
                        credential_id=credential.id,
                        purpose="mcp_factory",
                    )
                    servers[provider.value] = cfg.mcp_factory(
                        resolved.payload.access_token,
                        dict(metadata.raw),
                    )
                except Exception as exc:
                    self._logger.warning(
                        "mcp.factory_failed",
                        provider=provider.value,
                        error=str(exc),
                    )

        return servers
