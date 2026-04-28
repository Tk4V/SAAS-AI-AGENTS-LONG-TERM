"""TokenResolver — the only place tokens are decrypted.

Agents and API clients call `resolve(user_id, kind)` and receive a plaintext
access token ready to use. Everything else — DB session lifecycle, Fernet
decryption, refresh-on-expiry — is hidden inside.

This is the *new* resolver that lives alongside `oauth/token_resolver.py`.
Once the migration is done, the old one is deleted and routes/services
import from this path.
"""

from __future__ import annotations

from src.db.queries.user_credential_query import UserOAuthCredentialRepository
from src.db.session import Database, db
from src.integrations._shared.kinds import IntegrationKind
from src.utils.crypto import TokenCipher


class TokenResolver:
    """Fetches an OAuth credential from the DB and returns the plaintext token.

    Caller never sees the encrypted form, never sees the cipher, never sees the
    DB session. If the token is missing the resolver raises `NotFoundError`
    from the repository layer; that is the right signal for routes to return
    a 404 / "please reconnect this integration".

    Auto-refresh hook: when the credential row carries `expires_at` and that
    moment has passed, the resolver should call `OAuthAdapter.refresh()`,
    upsert the new bundle, and return the fresh access token. The wiring for
    this is added in the migration PR; the GitHub OAuth App (current pilot)
    issues non-expiring tokens, so refresh is a no-op for now.
    """

    def __init__(
        self,
        *,
        database: Database | None = None,
        cipher: TokenCipher | None = None,
    ) -> None:
        self._database = database or db
        self._cipher = cipher

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
            return cipher.decrypt(credential.token_encrypted)
