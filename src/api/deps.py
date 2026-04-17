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

from src.common.crypto import TokenCipher
from src.common.exceptions import AuthenticationError
from src.db.queries.memory_queries import MemoryRepository
from src.db.queries.project_queries import ProjectRepository
from src.db.queries.task_queries import TaskRepository
from src.db.queries.user_credential_queries import UserOAuthCredentialRepository
from src.db.session import db
from src.engine import PipelineExecutor
from src.engine import runtime as engine_runtime
from src.memory.chunkers import CodeChunker
from src.memory.embeddings import EmbeddingClient
from src.memory.episodic import EpisodicMemory
from src.memory.manager import MemoryManager
from src.memory.semantic import SemanticMemory
from src.services.auth_service import AuthService, CurrentUser
from src.services.oauth_service import OAuthService, OAuthStateSigner
from src.services.project_service import ProjectService
from src.services.task_service import TaskService
from src.services.webhook_service import WebhookService
from src.tools import toolbox as tool_singleton
from src.tools.git.factory import GitProviderFactory
from src.tools.llm.gateway import LLMGateway
from src.tools.sandbox.runner import SandboxRunner

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


def get_project_service(repo: ProjectRepositoryDep) -> ProjectService:
    return ProjectService(repo)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


def get_task_repository(session: SessionDep) -> TaskRepository:
    return TaskRepository(session)


TaskRepositoryDep = Annotated[TaskRepository, Depends(get_task_repository)]


def get_task_service(
    repo: TaskRepositoryDep,
    project_repo: ProjectRepositoryDep,
) -> TaskService:
    return TaskService(repository=repo, project_repository=project_repo)


TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]


def get_pipeline_executor() -> PipelineExecutor:
    """Returns the lazily-compiled engine executor built from the live runtime."""
    return engine_runtime.executor


PipelineExecutorDep = Annotated[PipelineExecutor, Depends(get_pipeline_executor)]


def get_llm_gateway() -> LLMGateway:
    return tool_singleton.llm


LLMGatewayDep = Annotated[LLMGateway, Depends(get_llm_gateway)]


def get_git_factory() -> GitProviderFactory:
    return tool_singleton.git


GitFactoryDep = Annotated[GitProviderFactory, Depends(get_git_factory)]


def get_sandbox_runner() -> SandboxRunner:
    return tool_singleton.sandbox


SandboxRunnerDep = Annotated[SandboxRunner, Depends(get_sandbox_runner)]


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


# -- Memory layer --


def get_embedding_client() -> EmbeddingClient:
    return tool_singleton.embedder


EmbeddingClientDep = Annotated[EmbeddingClient, Depends(get_embedding_client)]


def get_memory_repository(session: SessionDep) -> MemoryRepository:
    return MemoryRepository(session)


MemoryRepositoryDep = Annotated[MemoryRepository, Depends(get_memory_repository)]


def get_memory_manager(
    repo: MemoryRepositoryDep,
    embedder: EmbeddingClientDep,
) -> MemoryManager:
    episodic = EpisodicMemory(repository=repo, embedder=embedder)
    semantic = SemanticMemory(repository=repo, embedder=embedder, chunker=CodeChunker())
    return MemoryManager(episodic=episodic, semantic=semantic)


MemoryManagerDep = Annotated[MemoryManager, Depends(get_memory_manager)]


# -- Webhook service --


def get_webhook_service() -> WebhookService:
    return WebhookService()


WebhookServiceDep = Annotated[WebhookService, Depends(get_webhook_service)]
