"""Minimal contract every Clyde agent fulfills.

For agents that need to launch a Claude Agent SDK session, inherit from
``SDKAgent`` (in ``sdk_agent.py``) instead — it adds the SDK configuration
contract on top of this base.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

if TYPE_CHECKING:
    from uuid import UUID

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
from src.db.models.agent_config import MCPServerConfig
from src.db.models.credential import CredentialKind
from src.db.models.project import ProviderKind
from src.db.queries.agent_config_query import AgentConfigRepository
from src.db.queries.credential_event_query import CredentialEventRepository
from src.db.queries.credential_query import CredentialRepository
from src.db.session import db
from src.integrations._shared import (
    AuthlibClientFactory,
    OAuthAdapter,
    OAuthStateSigner,
    ProviderCatalog,
)
from src.services.agent_config_service import AgentConfigService


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

    async def resolve_google_token(self, *, user_id: int) -> str:
        """Fetch the user's Google OAuth access token."""
        return await self._token_provider.get_access_token(
            user_id=user_id, provider=ProviderKind.GOOGLE
        )

    async def resolve_azure_credentials(self, *, user_id: int) -> dict[str, str]:
        """Fetch Azure service principal credentials from the BEARER credential store.

        Expects a credential with ``metadata_json["provider"] == "azure"`` whose
        encrypted token is a JSON string:
        ``{"client_id": "...", "client_secret": "...", "tenant_id": "...", "subscription_id": "..."}``.

        Raises ``ValueError`` if no active Azure credential exists for this user.
        """
        async with db.session_scope() as session:
            cred_repo = CredentialRepository(session)
            bearer_creds = await cred_repo.list_active_bearer_with_provider(user_id=user_id)
            azure_cred = next(
                (c for c in bearer_creds if c.metadata_json.get("provider") == "azure"),
                None,
            )
            if azure_cred is None:
                raise ValueError("No active Azure credential found for user.")
            resolver = CredentialResolver(
                repository=cred_repo,
                cipher=self._ctx.cipher,
                kinds=get_kind_registry(),
                auditor=CredentialAuditor(CredentialEventRepository(session)),
            )
            resolved = await resolver.resolve(
                user_id=user_id,
                credential_id=azure_cred.id,
                purpose="azure_cli",
            )
        return json.loads(resolved.payload.token)

    async def build_in_process_mcp_servers(
        self,
        user_id: int | None,
        task_id: "UUID | None" = None,
    ) -> dict[str, Any]:
        """Return additional MCP servers that run inside the Python process.

        Default attaches ``clyde_chat`` whenever both ``user_id`` and
        ``task_id`` are known so every agent gets the ``ask_user`` tool
        for free. Subclasses extend the dict to register provider-specific
        skills (e.g. ``clyde_github``) on top of this baseline.
        ``run_sdk_session`` merges the result with the DB-driven remote
        MCPs from ``build_user_mcp_servers`` before handing the union to
        ``ClaudeAgentOptions``.
        """
        from src.agent_tools.custom_tools import (
            CLYDE_CHAT_SERVER_NAME,
            build_chat_skills_server,
        )

        if user_id is None or task_id is None:
            return {}
        return {
            CLYDE_CHAT_SERVER_NAME: build_chat_skills_server(
                task_id=task_id,
                user_id=user_id,
                agent_name=self.name,
            ),
        }

    async def build_user_mcp_servers(self, *, user_id: int) -> dict[str, Any]:
        """Mount MCP servers for every integration the user has connected.

        Loads MCP connection configs from the ``mcp_server_configs`` table,
        resolves the user's active credentials, injects the live token into
        each config, and returns a dict ready for ``ClaudeAgentOptions.mcp_servers``
        keyed by provider name (e.g. ``"github"``, ``"jira"``).
        """
        kinds = get_kind_registry()
        cipher = self._ctx.cipher
        svc = AgentConfigService()

        async with db.session_scope() as session:
            cfg_repo = AgentConfigRepository(session)
            cred_repo = CredentialRepository(session)
            events = CredentialEventRepository(session)
            auditor = CredentialAuditor(events=events)
            refresher = OAuthRefresher(
                repository=cred_repo,
                adapter=self._oauth_adapter,
                cipher=cipher,
                handler=kinds.get(CredentialKind.OAUTH),
            )
            resolver = CredentialResolver(
                repository=cred_repo,
                cipher=cipher,
                kinds=kinds,
                auditor=auditor,
                oauth_refresher=refresher,
            )

            mcp_configs = await cfg_repo.list_active_mcp_configs()
            config_map: dict[str, MCPServerConfig] = {c.provider_name: c for c in mcp_configs}

            servers: dict[str, Any] = {}

            # OAuth credentials (GitHub, Jira, Slack, …)
            oauth_credentials = await cred_repo.list_active_oauth_for_user(user_id=user_id)
            for credential in oauth_credentials:
                metadata = OAuthMetadata(**credential.metadata_json)
                provider_name = metadata.provider
                mcp_cfg = config_map.get(provider_name)
                if mcp_cfg is None:
                    continue
                try:
                    resolved = await resolver.resolve(
                        user_id=user_id,
                        credential_id=credential.id,
                        purpose="mcp_server",
                    )
                    token = resolved.payload.access_token
                    servers[provider_name] = svc.build_mcp_server_entry(config=mcp_cfg, token=token)
                except Exception as exc:
                    self._logger.warning(
                        "mcp.build_failed",
                        provider=provider_name,
                        error=str(exc),
                    )

            # Bearer credentials (AWS, …)
            bearer_credentials = await cred_repo.list_active_bearer_with_provider(user_id=user_id)
            for credential in bearer_credentials:
                provider_name = credential.metadata_json.get("provider")
                if not provider_name or provider_name in servers:
                    continue
                mcp_cfg = config_map.get(provider_name)
                if mcp_cfg is None:
                    continue
                try:
                    resolved = await resolver.resolve(
                        user_id=user_id,
                        credential_id=credential.id,
                        purpose="mcp_server",
                    )
                    token = self._build_bearer_token(
                        provider_name=provider_name,
                        raw_token=resolved.payload.token,
                        metadata=credential.metadata_json,
                    )
                    servers[provider_name] = svc.build_mcp_server_entry(config=mcp_cfg, token=token)
                except Exception as exc:
                    self._logger.warning(
                        "mcp.build_failed",
                        provider=provider_name,
                        error=str(exc),
                    )

        return servers

    def _build_bearer_token(
        self,
        *,
        provider_name: str,
        raw_token: str,
        metadata: dict[str, Any],
    ) -> str:
        """Prepare the token value to inject into a bearer credential's MCP config.

        For most providers this is the raw token. For AWS, the IAM credentials
        stored in ``raw_token`` are packed into a short-lived HS256 JWT that
        the backend SigV4 proxy validates before signing the forwarded request.
        """
        if provider_name != "aws":
            return raw_token

        from joserfc import jwt
        from joserfc.jwk import OctKey

        creds: dict[str, str] = json.loads(raw_token)
        region: str = metadata.get("region", "us-east-1")
        now = int(time.time())
        key = OctKey.import_key(self._ctx.settings.jwt_secret.get_secret_value().encode())
        return jwt.encode(
            {"alg": "HS256"},
            {
                "iat": now,
                "exp": now + 3600,
                "access_key_id": creds["access_key_id"],
                "secret_access_key": creds["secret_access_key"],
                "region": region,
            },
            key,
        )
