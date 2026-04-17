"""OAuth flow orchestration for third-party integrations.

The frontend calls `start_flow`, gets an authorization URL with a signed state
token, and redirects the browser to the provider. The provider sends the user
back to our callback endpoint, which forwards code and state to
`handle_callback`. We verify the state, swap the code for an access token,
encrypt it, and persist it as a `UserOAuthCredential`.

Other services that need to act on behalf of the user (Tech Lead cloning a
repo, Release Manager creating a PR) ask `OAuthService.get_token` for the
plaintext token at the moment of use.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import structlog

from src.common.crypto import TokenCipher
from src.common.exceptions import AuthenticationError, ExternalServiceError
from src.config import Settings, get_settings
from src.db.models.project import GitProviderKind
from src.db.models.user_credential import UserOAuthCredential
from src.db.queries.user_credential_queries import UserOAuthCredentialRepository
from src.tools.git.factory import GitProviderFactory
from src.tools.git.providers.github import GitHubProvider


class OAuthStateSigner:
    """Signs and verifies the `state` parameter that survives the OAuth round trip.

    The state is a short-lived JWT carrying the user_id and the provider, so
    the callback can attribute the granted token to the right user without
    keeping any server-side session.
    """

    TOKEN_TYPE = "oauth_state"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def sign(self, *, user_id: int, provider: str) -> str:
        now = datetime.now(UTC)
        payload = {
            "type": self.TOKEN_TYPE,
            "user_id": user_id,
            "provider": provider,
            "nonce": secrets.token_urlsafe(16),
            "iat": int(now.timestamp()),
            "exp": int(
                (now + timedelta(seconds=self._settings.oauth_state_ttl_sec)).timestamp()
            ),
        }
        return jwt.encode(
            payload,
            self._settings.jwt_secret.get_secret_value(),
            algorithm=self._settings.jwt_algorithm,
        )

    def verify(self, state: str) -> dict[str, Any]:
        try:
            claims = jwt.decode(
                state,
                self._settings.jwt_secret.get_secret_value(),
                algorithms=[self._settings.jwt_algorithm],
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("OAuth state has expired.") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("OAuth state is invalid.") from exc

        if claims.get("type") != self.TOKEN_TYPE:
            raise AuthenticationError("Token presented as OAuth state is not one.")
        if not isinstance(claims.get("user_id"), int):
            raise AuthenticationError("OAuth state is missing user_id.")
        return claims


class OAuthService:
    def __init__(
        self,
        *,
        repository: UserOAuthCredentialRepository,
        git_factory: GitProviderFactory,
        cipher: TokenCipher,
        state_signer: OAuthStateSigner,
        settings: Settings | None = None,
    ) -> None:
        self._repo = repository
        self._git = git_factory
        self._cipher = cipher
        self._state_signer = state_signer
        self._settings = settings or get_settings()
        self._logger = structlog.get_logger("clyde.oauth")

    def start_flow(
        self,
        *,
        user_id: int,
        provider: GitProviderKind,
    ) -> str:
        """Build the provider's authorization URL. State is embedded in the URL."""
        state = self._state_signer.sign(user_id=user_id, provider=provider.value)
        provider_impl = self._git.for_kind(provider)
        url = provider_impl.authorization_url(
            state=state,
            redirect_uri=self._callback_url(provider),
        )
        self._logger.info("oauth.start", user_id=user_id, provider=provider.value)
        return url

    async def handle_callback(
        self,
        *,
        provider: GitProviderKind,
        code: str,
        state: str,
    ) -> UserOAuthCredential:
        claims = self._state_signer.verify(state)
        if claims.get("provider") != provider.value:
            raise AuthenticationError("OAuth state does not match the callback provider.")

        user_id: int = claims["user_id"]
        provider_impl = self._git.for_kind(provider)
        token = await provider_impl.exchange_code_for_token(
            code=code,
            redirect_uri=self._callback_url(provider),
        )

        encrypted = self._cipher.encrypt(token)
        credential = await self._repo.upsert(
            user_id=user_id,
            provider=provider,
            token_encrypted=encrypted,
            scopes=self._scopes_for(provider),
        )
        self._logger.info(
            "oauth.callback.success",
            user_id=user_id,
            provider=provider.value,
        )
        return credential

    async def list_for_user(self, *, user_id: int) -> list[UserOAuthCredential]:
        return await self._repo.list_for_user(user_id=user_id)

    async def get_token(
        self,
        *,
        user_id: int,
        provider: GitProviderKind,
    ) -> str:
        credential = await self._repo.get(user_id=user_id, provider=provider)
        return self._cipher.decrypt(credential.token_encrypted)

    async def list_repos(
        self,
        *,
        user_id: int,
        provider: GitProviderKind,
    ) -> list[dict]:
        """Fetch the user's repositories from the connected provider."""
        token = await self.get_token(user_id=user_id, provider=provider)
        provider_impl = self._git.for_kind(provider)
        return await provider_impl.list_repos(token=token)

    async def revoke(
        self,
        *,
        user_id: int,
        provider: GitProviderKind,
    ) -> None:
        credential = await self._repo.get(user_id=user_id, provider=provider)
        token = self._cipher.decrypt(credential.token_encrypted)
        provider_impl = self._git.for_kind(provider)
        try:
            await provider_impl.revoke_token(token=token)
        except ExternalServiceError as exc:
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

    def _callback_url(self, provider: GitProviderKind) -> str:
        return (
            f"{self._settings.oauth_callback_base_url.rstrip('/')}"
            f"{self._settings.api_prefix}/auth/oauth/{provider.value}/callback"
        )

    @staticmethod
    def _scopes_for(provider: GitProviderKind) -> str:
        if provider is GitProviderKind.GITHUB:
            return GitHubProvider.DEFAULT_SCOPES
        return ""
