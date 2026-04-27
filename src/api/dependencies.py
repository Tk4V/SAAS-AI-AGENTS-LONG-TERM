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

from src.utils.crypto import TokenCipher
from src.utils.exceptions import AuthenticationError
from src.db.queries.project_query import ProjectRepository
from src.db.queries.task_query import TaskRepository
from src.db.queries.user_credential_query import UserOAuthCredentialRepository
from src.db.session import db
from src.services.auth_service import AuthService, CurrentUser
from src.services.oauth_service import OAuthService, OAuthStateSigner
from src.services.project_service import ProjectService
from src.services.task_service import TaskService
from src.integrations import toolbox as tool_singleton
from src.integrations.git.factory import GitProviderFactory

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


def get_git_factory() -> GitProviderFactory:
    return tool_singleton.git


GitFactoryDep = Annotated[GitProviderFactory, Depends(get_git_factory)]


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
    return tool_singleton.cipher


TokenCipherDep = Annotated[TokenCipher, Depends(get_token_cipher)]


def get_user_credential_repository(session: SessionDep) -> UserOAuthCredentialRepository:
    return UserOAuthCredentialRepository(session)


UserOAuthCredentialRepositoryDep = Annotated[
    UserOAuthCredentialRepository,
    Depends(get_user_credential_repository),
]


def get_oauth_state_signer() -> OAuthStateSigner:
    return OAuthStateSigner()


OAuthStateSignerDep = Annotated[OAuthStateSigner, Depends(get_oauth_state_signer)]


def get_oauth_service(
    repository: UserOAuthCredentialRepositoryDep,
    git_factory: GitFactoryDep,
    cipher: TokenCipherDep,
    state_signer: OAuthStateSignerDep,
) -> OAuthService:
    return OAuthService(
        repository=repository,
        git_factory=git_factory,
        cipher=cipher,
        state_signer=state_signer,
    )


OAuthServiceDep = Annotated[OAuthService, Depends(get_oauth_service)]


def get_project_service(
    repo: ProjectRepositoryDep,
    oauth: OAuthServiceDep,
    git_factory: GitFactoryDep,
) -> ProjectService:
    return ProjectService(repository=repo, oauth=oauth, git_factory=git_factory)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]
