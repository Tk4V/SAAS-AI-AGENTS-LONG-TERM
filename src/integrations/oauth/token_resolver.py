"""Resolves and decrypts OAuth tokens for pipeline agents.

When an agent needs to authenticate with a provider (e.g., GitHub), it calls
TokenResolver to fetch the encrypted token from the database and decrypt it.
This keeps token-handling logic out of individual agents.
"""

from __future__ import annotations

from src.db.models.project import GitProviderKind
from src.db.queries.user_credential_query import UserOAuthCredentialRepository
from src.db.session import Database, db
from src.utils.crypto import TokenCipher


class TokenResolver:
    """Fetches and decrypts user OAuth tokens from the database.

    Agents call ``resolve()`` with a user_id and provider to get a plaintext
    token ready for API calls. The resolver handles database session lifecycle
    and Fernet decryption internally.
    """

    def __init__(
        self,
        *,
        database: Database | None = None,
        cipher: TokenCipher | None = None,
    ) -> None:
        """Create a token resolver.

        Args:
            database: Database instance for session creation. Defaults to global singleton.
            cipher: Cipher for decrypting stored tokens. Defaults to integrations.registry.toolbox.cipher.
        """
        self._database = database or db
        self._cipher = cipher

    def _get_cipher(self) -> TokenCipher:
        """Lazily resolve the cipher to avoid circular imports at init time."""
        if self._cipher is None:
            from src.integrations.registry import toolbox
            self._cipher = toolbox.cipher
        return self._cipher

    async def resolve(
        self,
        *,
        user_id: int,
        provider: GitProviderKind = GitProviderKind.GITHUB,
    ) -> str:
        """Fetch and decrypt the user's OAuth token for the given provider.

        Args:
            user_id: The Django user primary key.
            provider: The git provider to look up credentials for.

        Returns:
            The decrypted plaintext OAuth token.

        Raises:
            NotFoundError: If the user has no stored credential for this provider.
            CryptoDecryptError: If the stored token cannot be decrypted.
        """
        cipher = self._get_cipher()
        async with self._database.session_scope() as session:
            repository = UserOAuthCredentialRepository(session)
            credential = await repository.get(user_id=user_id, provider=provider)
            return cipher.decrypt(credential.token_encrypted)
