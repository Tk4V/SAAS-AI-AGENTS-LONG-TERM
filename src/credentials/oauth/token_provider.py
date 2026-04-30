"""Token provider for legacy callers that want a plain access-token string.

Replaces ``src.integrations._shared.token_resolver.TokenResolver`` so existing
``BaseApiClient``-based clients (``GitHubApiClient``, future ``JiraApiClient``)
do not have to know about the credentials domain. Internally it delegates to
``CredentialRepository`` + ``CredentialResolver`` so refresh-on-expiry comes
for free.

The provider opens its own session via ``Database.session_scope`` because most
callers are coroutines outside of a FastAPI request scope (background agents,
MCP factories, CLI scripts). FastAPI routes that already have a session can
build a resolver directly from DI instead of using this provider.
"""

from __future__ import annotations

from src.credentials.audit import CredentialAuditor
from src.credentials.kinds.registry import OAuthKindHandler, get_kind_registry
from src.credentials.oauth.refresher import OAuthRefresher
from src.credentials.resolver import CredentialResolver
from src.db.models.credential import CredentialKind
from src.db.models.project import ProviderKind
from src.db.queries.credential_event_query import CredentialEventRepository
from src.db.queries.credential_query import CredentialRepository
from src.db.session import Database, db
from src.integrations._shared.adapter import OAuthAdapter
from src.utils.crypto import TokenCipher
from src.utils.exceptions import NotFoundError


class OAuthTokenProvider:
    """Returns a plaintext access token for a connected OAuth provider.

    Drop-in replacement for the legacy ``TokenResolver`` API. The constructor
    takes the OAuth adapter (for refresh) and the cipher; the database is
    pulled from the module-level singleton so callers do not have to pass it
    in. Tests should construct their own ``Database`` and pass it via the
    ``database`` keyword.
    """

    def __init__(
        self,
        *,
        adapter: OAuthAdapter,
        cipher: TokenCipher,
        database: Database | None = None,
    ) -> None:
        self._adapter = adapter
        self._cipher = cipher
        self._database = database or db
        self._kinds = get_kind_registry()
        self._oauth_handler: OAuthKindHandler = self._kinds.get(CredentialKind.OAUTH)

    async def get_access_token(
        self,
        *,
        user_id: int,
        provider: ProviderKind,
    ) -> str:
        async with self._database.session_scope() as session:
            repo = CredentialRepository(session)
            events = CredentialEventRepository(session)
            auditor = CredentialAuditor(events=events)
            refresher = OAuthRefresher(
                repository=repo,
                adapter=self._adapter,
                cipher=self._cipher,
                handler=self._oauth_handler,
            )
            resolver = CredentialResolver(
                repository=repo,
                cipher=self._cipher,
                kinds=self._kinds,
                auditor=auditor,
                oauth_refresher=refresher,
            )
            resolved = await resolver.resolve_oauth_for_provider(
                user_id=user_id,
                provider=provider.value,
                purpose=f"api_call:{provider.value}",
            )
            if resolved is None:
                raise NotFoundError(
                    f"No {provider.value} OAuth credential connected for this user."
                )
            access_token: str = resolved.payload.access_token
            return access_token
