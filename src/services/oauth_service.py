"""OAuth flow orchestration for third-party integrations.

The frontend calls `start_flow`, gets an authorization URL with a signed
state token, and redirects the browser to the provider. The provider sends
the user back to the callback endpoint, which forwards `code` and `state`
to `handle_callback`. We verify the state, swap the code for tokens via
`OAuthAdapter`, encrypt them, and persist as `UserOAuthCredential`.

This service is a thin orchestrator. All OAuth protocol details
(authorize URL building, code-for-token exchange, refresh, revocation
dispatch) live in `src.integrations._shared.OAuthAdapter`. Provider-specific
details live in `src/integrations/<name>/`. This file only knows about
encryption and persistence.
"""

from __future__ import annotations

import structlog

from src.config import Settings, get_settings
from src.db.models.project import ProviderKind
from src.db.models.user_credential import UserOAuthCredential
from src.db.queries.user_credential_query import UserOAuthCredentialRepository
from src.integrations._shared import (
    OAuthAdapter,
    ProviderApiError,
    ProviderAuthError,
    ProviderCatalog,
)
from src.integrations.github import GitHubApiClient
from src.integrations.github.git_ops import RepoCoordinates
from src.utils.crypto import TokenCipher
from src.utils.exceptions import AuthenticationError


class OAuthService:
    def __init__(
        self,
        *,
        repository: UserOAuthCredentialRepository,
        adapter: OAuthAdapter,
        catalog: ProviderCatalog,
        cipher: TokenCipher,
        settings: Settings | None = None,
    ) -> None:
        self._repo = repository
        self._adapter = adapter
        self._catalog = catalog
        self._cipher = cipher
        self._settings = settings or get_settings()
        self._logger = structlog.get_logger("clyde.oauth")

    def start_flow(
        self,
        *,
        user_id: int,
        provider: ProviderKind,
    ) -> str:
        """Build the provider's authorization URL. State is embedded in the URL."""
        request = self._adapter.build_authorize_request(
            kind=provider,
            user_id=user_id,
            redirect_uri=self._callback_url(provider),
        )
        self._logger.info("oauth.start", user_id=user_id, provider=provider.value)
        return request.url

    async def handle_callback(
        self,
        *,
        provider: ProviderKind,
        code: str,
        state: str,
    ) -> UserOAuthCredential:
        try:
            result = await self._adapter.handle_callback(
                kind=provider,
                code=code,
                state=state,
                redirect_uri=self._callback_url(provider),
            )
        except ProviderAuthError as exc:
            raise AuthenticationError(str(exc)) from exc

        bundle = result.token
        encrypted_access = self._cipher.encrypt(bundle.access_token)
        encrypted_refresh = (
            self._cipher.encrypt(bundle.refresh_token)
            if bundle.refresh_token is not None
            else None
        )

        raw_metadata = dict(bundle.raw)

        cfg = self._catalog.get(provider)
        if cfg.post_callback_hook is not None:
            try:
                extra = await cfg.post_callback_hook(bundle.access_token)
                raw_metadata.update(extra)
                self._logger.info(
                    "oauth.post_callback_hook.success",
                    user_id=result.user_id,
                    provider=provider.value,
                    keys=list(extra.keys()),
                )
            except Exception as exc:
                # Non-fatal: credential is saved without the extra metadata.
                # The relevant MCP server will be skipped by build_user_mcp_servers.
                self._logger.warning(
                    "oauth.post_callback_hook.failed",
                    user_id=result.user_id,
                    provider=provider.value,
                    error=str(exc),
                )

        credential = await self._repo.upsert(
            user_id=result.user_id,
            provider=provider,
            token_encrypted=encrypted_access,
            refresh_token_encrypted=encrypted_refresh,
            expires_at=bundle.expires_at,
            scopes=",".join(bundle.scopes),
            raw_metadata=raw_metadata,
        )
        self._logger.info(
            "oauth.callback.success",
            user_id=result.user_id,
            provider=provider.value,
        )
        return credential

    async def list_for_user(self, *, user_id: int) -> list[UserOAuthCredential]:
        return await self._repo.list_for_user(user_id=user_id)

    async def get_token(
        self,
        *,
        user_id: int,
        provider: ProviderKind,
    ) -> str:
        credential = await self._repo.get(user_id=user_id, provider=provider)
        return self._cipher.decrypt(credential.token_encrypted)

    async def list_repos(
        self,
        *,
        user_id: int,
        provider: ProviderKind,
    ) -> list[dict]:
        """Fetch the user's repositories from the connected provider.

        Today only GitHub is wired; once another provider's API client lands
        we will dispatch by `provider` here. Keeping this in the service is a
        temporary convenience for the frontend integrations page.
        """
        if provider is ProviderKind.GITHUB:
            from src.integrations._shared.token_resolver import TokenResolver
            resolver = TokenResolver(cipher=self._cipher, adapter=self._adapter)
            api = GitHubApiClient(user_id=user_id, token_resolver=resolver)
            try:
                return await api.list_repos()
            finally:
                await api.aclose()
        raise NotImplementedError(f"list_repos not wired for {provider.value}.")

    async def list_branches(
        self,
        *,
        user_id: int,
        provider: ProviderKind,
        repo_url: str,
    ) -> list[str]:
        """Fetch branch names for a repository at the given provider."""
        if provider is ProviderKind.GITHUB:
            from src.integrations._shared.token_resolver import TokenResolver
            from src.integrations.github.git_ops import GitHubGitOps
            resolver = TokenResolver(cipher=self._cipher, adapter=self._adapter)
            coordinates = GitHubGitOps.parse_repo_url(repo_url)
            api = GitHubApiClient(user_id=user_id, token_resolver=resolver)
            try:
                return await api.list_branches(coordinates=coordinates)
            finally:
                await api.aclose()
        raise NotImplementedError(f"list_branches not wired for {provider.value}.")

    async def revoke(
        self,
        *,
        user_id: int,
        provider: ProviderKind,
    ) -> None:
        credential = await self._repo.get(user_id=user_id, provider=provider)
        token = self._cipher.decrypt(credential.token_encrypted)
        try:
            await self._adapter.revoke(kind=provider, access_token=token)
        except ProviderApiError as exc:
            self._logger.warning(
                "oauth.revoke.remote_failed",
                user_id=user_id,
                provider=provider.value,
                error=str(exc),
            )
        await self._repo.delete(user_id=user_id, provider=provider)
        self._logger.info(
            "oauth.revoke.completed", user_id=user_id, provider=provider.value
        )

    def _callback_url(self, provider: ProviderKind) -> str:
        return (
            f"{self._settings.oauth_callback_base_url.rstrip('/')}"
            f"{self._settings.api_prefix}/auth/oauth/{provider.value}/callback"
        )

    def build_callback_redirect_url(
        self,
        *,
        provider: ProviderKind,
        success: bool,
        error_code: str | None = None,
    ) -> str:
        """Build the URL to redirect the browser to after OAuth callback."""
        base = self._settings.frontend_redirect_url.rstrip("?&")
        separator = "&" if "?" in base else "?"
        if success:
            return f"{base}{separator}integration={provider.value}&status=ok"
        return (
            f"{base}{separator}integration={provider.value}"
            f"&status=error&code={error_code or 'unknown'}"
        )
