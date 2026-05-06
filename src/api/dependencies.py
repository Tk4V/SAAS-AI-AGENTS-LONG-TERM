"""FastAPI dependency providers.

Each repository is built with the request-scoped session, each service is
built with its repository, and routes consume the service through a typed
`Annotated[..., Depends(...)]` alias. This keeps route handlers ignorant of
how their collaborators are constructed and keeps everything mockable in tests.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.app_context import app_context
from src.credentials.audit import CredentialAuditor
from src.credentials.catalog import (
    PublicProviderCatalog,
    get_public_provider_catalog,
)
from src.credentials.kinds import KindRegistry, get_kind_registry
from src.credentials.kinds.registry import OAuthKindHandler
from src.credentials.oauth.refresher import OAuthRefresher
from src.credentials.oauth.service import OAuthCredentialService
from src.credentials.oauth.token_provider import OAuthTokenProvider
from src.credentials.resolver import CredentialResolver
from src.credentials.service import CredentialService
from src.db.models.credential import CredentialKind
from src.db.queries.agent_config_query import AgentConfigRepository
from src.db.queries.agent_query import (
    AgentRepository,
    MCPServerRepository,
    SubagentAdminRepository,
    SystemToolRepository,
)
from src.db.queries.credential_event_query import CredentialEventRepository
from src.db.queries.credential_query import CredentialRepository
from src.db.queries.project_query import ProjectRepository
from src.db.queries.task_query import TaskRepository
from src.db.session import db
from src.integrations._shared import (
    AuthlibClientFactory,
    OAuthAdapter,
    OAuthStateSigner,
    ProviderCatalog,
)
from src.services.agent_service import AgentService
from src.services.auth_service import AuthService, CurrentUser
from src.services.project_service import ProjectService
from src.services.task_service import TaskService
from src.utils.crypto import TokenCipher
from src.config import get_settings
from src.utils.exceptions import AuthenticationError, AuthorizationError

bearer_scheme = HTTPBearer(
    bearerFormat="JWT",
    description="JWT access token issued by the Django DRF service.",
    auto_error=False,
)


SessionDep = Annotated[AsyncSession, Depends(db.get_session)]


def get_auth_service() -> AuthService:
    return AuthService()


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    auth: AuthServiceDep,
) -> CurrentUser:
    """Resolve the caller from the Authorization: Bearer header."""
    if credentials is None:
        raise AuthenticationError(
            "Missing Authorization header.",
            details={"expected": "Authorization: Bearer <jwt>"},
        )
    return auth.current_user_from_token(credentials.credentials)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def _parse_admin_ids(raw: str) -> set[int]:
    """Parse a comma-separated list of admin user ids from settings."""
    return {int(part) for part in raw.split(",") if part.strip().isdigit()}


async def require_admin(user: CurrentUserDep) -> CurrentUser:
    """Reject the request unless the caller is an administrator.

    Recognises two signals: an explicit ``is_admin`` claim in the JWT
    (preferred — once Django adds it) or the user id appearing in the
    ``admin_user_ids`` settings allowlist (the current stand-in).
    """
    claims = user.raw_claims or {}
    if claims.get("is_admin") is True:
        return user
    if user.id in _parse_admin_ids(get_settings().admin_user_ids):
        return user
    raise AuthorizationError(
        "Administrator privileges are required for this endpoint.",
        details={"required": "is_admin"},
    )


AdminUserDep = Annotated[CurrentUser, Depends(require_admin)]


async def get_current_user_ws(websocket: WebSocket) -> CurrentUser:
    """Resolve the caller for a WebSocket connection.

    The token is taken from the `?token=` query parameter, which is the
    standard pattern when browsers cannot set custom headers on the WS
    handshake. The connection is closed if validation fails so the client
    can distinguish auth errors from generic disconnects.
    """
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        raise AuthenticationError(
            "Missing token query parameter on WebSocket handshake.",
        )
    auth = AuthService()
    try:
        return auth.current_user_from_token(token)
    except AuthenticationError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        raise


def get_project_repository(session: SessionDep) -> ProjectRepository:
    return ProjectRepository(session)


ProjectRepositoryDep = Annotated[ProjectRepository, Depends(get_project_repository)]


def get_task_repository(session: SessionDep) -> TaskRepository:
    return TaskRepository(session)


TaskRepositoryDep = Annotated[TaskRepository, Depends(get_task_repository)]


def get_task_service(
    repo: TaskRepositoryDep,
    project_repo: ProjectRepositoryDep,
) -> TaskService:
    return TaskService(repository=repo, project_repository=project_repo)


TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]


def get_token_cipher() -> TokenCipher:
    return app_context.cipher


TokenCipherDep = Annotated[TokenCipher, Depends(get_token_cipher)]


# OAuth framework — built once per request from process-wide singletons.
# `ProviderCatalog` is stateless; `AuthlibClientFactory` caches httpx pools
# per provider; `OAuthStateSigner` is cheap to construct. Reusing process-
# level instances would let one request leak Authlib client state to
# another, so we keep them request-scoped.

def get_provider_catalog() -> ProviderCatalog:
    return ProviderCatalog()


ProviderCatalogDep = Annotated[ProviderCatalog, Depends(get_provider_catalog)]


def get_oauth_adapter(catalog: ProviderCatalogDep) -> OAuthAdapter:
    factory = AuthlibClientFactory(catalog_lookup=catalog.get)
    signer = OAuthStateSigner()
    return OAuthAdapter(
        catalog_lookup=catalog.get,
        client_factory=factory,
        state_signer=signer,
    )


OAuthAdapterDep = Annotated[OAuthAdapter, Depends(get_oauth_adapter)]


def get_oauth_token_provider(
    adapter: OAuthAdapterDep,
    cipher: TokenCipherDep,
) -> OAuthTokenProvider:
    return OAuthTokenProvider(adapter=adapter, cipher=cipher)


OAuthTokenProviderDep = Annotated[
    OAuthTokenProvider, Depends(get_oauth_token_provider)
]


def get_project_service(
    repo: ProjectRepositoryDep,
    token_provider: OAuthTokenProviderDep,
) -> ProjectService:
    return ProjectService(repository=repo, oauth_token_provider=token_provider)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


def get_credential_repository(session: SessionDep) -> CredentialRepository:
    return CredentialRepository(session)


CredentialRepositoryDep = Annotated[
    CredentialRepository, Depends(get_credential_repository)
]


def get_credential_event_repository(session: SessionDep) -> CredentialEventRepository:
    return CredentialEventRepository(session)


CredentialEventRepositoryDep = Annotated[
    CredentialEventRepository, Depends(get_credential_event_repository)
]


def get_credential_auditor(
    events: CredentialEventRepositoryDep,
) -> CredentialAuditor:
    return CredentialAuditor(events=events)


CredentialAuditorDep = Annotated[CredentialAuditor, Depends(get_credential_auditor)]


def get_kind_registry_dep() -> KindRegistry:
    return get_kind_registry()


KindRegistryDep = Annotated[KindRegistry, Depends(get_kind_registry_dep)]


def get_credential_service(
    repo: CredentialRepositoryDep,
    cipher: TokenCipherDep,
    kinds: KindRegistryDep,
    auditor: CredentialAuditorDep,
) -> CredentialService:
    return CredentialService(
        repository=repo,
        cipher=cipher,
        kinds=kinds,
        auditor=auditor,
    )


CredentialServiceDep = Annotated[CredentialService, Depends(get_credential_service)]


def get_oauth_kind_handler(kinds: KindRegistryDep) -> OAuthKindHandler:
    handler = kinds.get(CredentialKind.OAUTH)
    return handler  # type: ignore[no-any-return]


OAuthKindHandlerDep = Annotated[OAuthKindHandler, Depends(get_oauth_kind_handler)]


def get_oauth_refresher(
    repo: CredentialRepositoryDep,
    adapter: OAuthAdapterDep,
    cipher: TokenCipherDep,
    handler: OAuthKindHandlerDep,
) -> OAuthRefresher:
    return OAuthRefresher(
        repository=repo,
        adapter=adapter,
        cipher=cipher,
        handler=handler,
    )


OAuthRefresherDep = Annotated[OAuthRefresher, Depends(get_oauth_refresher)]


def get_credential_resolver(
    repo: CredentialRepositoryDep,
    cipher: TokenCipherDep,
    kinds: KindRegistryDep,
    auditor: CredentialAuditorDep,
    refresher: OAuthRefresherDep,
) -> CredentialResolver:
    return CredentialResolver(
        repository=repo,
        cipher=cipher,
        kinds=kinds,
        auditor=auditor,
        oauth_refresher=refresher,
    )


CredentialResolverDep = Annotated[CredentialResolver, Depends(get_credential_resolver)]


def get_oauth_credential_service(
    repo: CredentialRepositoryDep,
    adapter: OAuthAdapterDep,
    catalog: ProviderCatalogDep,
    cipher: TokenCipherDep,
    auditor: CredentialAuditorDep,
    handler: OAuthKindHandlerDep,
) -> OAuthCredentialService:
    return OAuthCredentialService(
        repository=repo,
        adapter=adapter,
        catalog=catalog,
        cipher=cipher,
        auditor=auditor,
        handler=handler,
    )


OAuthCredentialServiceDep = Annotated[
    OAuthCredentialService, Depends(get_oauth_credential_service)
]


def get_public_provider_catalog_dep() -> PublicProviderCatalog:
    return get_public_provider_catalog()


PublicProviderCatalogDep = Annotated[
    PublicProviderCatalog, Depends(get_public_provider_catalog_dep)
]


def get_agent_config_repository(session: SessionDep) -> AgentConfigRepository:
    return AgentConfigRepository(session)


AgentConfigRepositoryDep = Annotated[
    AgentConfigRepository, Depends(get_agent_config_repository)
]


def get_agent_repository(session: SessionDep) -> AgentRepository:
    return AgentRepository(session)


AgentRepositoryDep = Annotated[AgentRepository, Depends(get_agent_repository)]


def get_subagent_admin_repository(session: SessionDep) -> SubagentAdminRepository:
    return SubagentAdminRepository(session)


SubagentAdminRepositoryDep = Annotated[
    SubagentAdminRepository, Depends(get_subagent_admin_repository)
]


def get_system_tool_repository(session: SessionDep) -> SystemToolRepository:
    return SystemToolRepository(session)


SystemToolRepositoryDep = Annotated[
    SystemToolRepository, Depends(get_system_tool_repository)
]


def get_mcp_server_repository(session: SessionDep) -> MCPServerRepository:
    return MCPServerRepository(session)


MCPServerRepositoryDep = Annotated[
    MCPServerRepository, Depends(get_mcp_server_repository)
]


def get_agent_service(
    repo: AgentRepositoryDep,
    subagent_repo: SubagentAdminRepositoryDep,
    mcp_repo: MCPServerRepositoryDep,
) -> AgentService:
    return AgentService(
        repository=repo,
        subagent_admin_repository=subagent_repo,
        mcp_repository=mcp_repo,
    )


AgentServiceDep = Annotated[AgentService, Depends(get_agent_service)]
