"""TokenResolver — the only place tokens are decrypted.

Agents and API clients call `resolve(user_id, kind)` and receive a plaintext
access token ready to use. Everything else — DB session lifecycle, Fernet
decryption, refresh-on-expiry — is hidden inside.

When an `OAuthAdapter` is injected and the stored credential has both an
`expires_at` and a `refresh_token_encrypted`, resolve() will transparently
refresh the token, persist the new bundle, and return the fresh access token.
Callers never need to know a refresh happened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.db.queries.user_credential_query import UserOAuthCredentialRepository
from src.db.session import Database, db
from src.integrations._shared.kinds import IntegrationKind
from src.integrations._shared.tokens import TokenBundle
from src.utils.crypto import TokenCipher

if TYPE_CHECKING:
    from src.integrations._shared.adapter import OAuthAdapter


class TokenResolver:
    """Fetches an OAuth credential from the DB and returns the plaintext token.

    Caller never sees the encrypted form, never sees the cipher, never sees the
    DB session. If the token is missing the resolver raises `NotFoundError`
    from the repository layer; that is the right signal for routes to return
    a 404 / "please reconnect this integration".

    When `adapter` is provided, expired tokens are refreshed automatically.
    The new tokens are persisted (only the token columns — account metadata is
    preserved) and the fresh access token is returned transparently.
    """

    def __init__(
        self,
        *,
        database: Database | None = None,
        cipher: TokenCipher | None = None,
        adapter: OAuthAdapter | None = None,
    ) -> None:
        self._database = database or db
        self._cipher = cipher
        self._adapter = adapter

    def _get_cipher(self) -> TokenCipher:
        if self._cipher is None:
            from src.app_context import app_context
            self._cipher = app_context.cipher
        return self._cipher

    async def resolve(
        self,
        *,
        user_id: int,
        kind: IntegrationKind = IntegrationKind.GITHUB,
    ) -> str:
        cipher = self._get_cipher()
        async with self._database.session_scope() as session:
            repository = UserOAuthCredentialRepository(session)
            credential = await repository.get(user_id=user_id, provider=kind)

            # Auto-refresh: transparently rotate expiring tokens when an adapter
            # is wired in. GitHub credentials have expires_at=None so this branch
            # is never entered for GitHub regardless of adapter presence.
            if (
                self._adapter is not None
                and credential.expires_at is not None
                and credential.refresh_token_encrypted is not None
                and TokenBundle(access_token="", expires_at=credential.expires_at).is_expired()
            ):
                plaintext_refresh = cipher.decrypt(credential.refresh_token_encrypted)
                new_bundle = await self._adapter.refresh(
                    kind=kind, refresh_token=plaintext_refresh
                )
                await repository.update_tokens(
                    user_id=user_id,
                    provider=kind,
                    token_encrypted=cipher.encrypt(new_bundle.access_token),
                    refresh_token_encrypted=(
                        cipher.encrypt(new_bundle.refresh_token)
                        if new_bundle.refresh_token is not None
                        else None
                    ),
                    expires_at=new_bundle.expires_at,
                    scopes=",".join(new_bundle.scopes),
                )
                return new_bundle.access_token

            return cipher.decrypt(credential.token_encrypted)
