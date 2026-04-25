"""Symmetric encryption for secrets we have to keep at rest.

OAuth access tokens for git providers and any future user-supplied API keys
go through this cipher before being written to Postgres. The Fernet key is
held in `FERNET_KEY` and must be 32 url-safe base64 bytes — generate one with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from src.utils.exceptions import AppError
from src.config import Settings, get_settings


class CryptoConfigError(AppError):
    """Raised when the application is started without a valid FERNET_KEY."""

    code = "crypto_config_error"
    http_status = 500


class CryptoDecryptError(AppError):
    """Raised when stored ciphertext cannot be decrypted with the current key."""

    code = "crypto_decrypt_error"
    http_status = 500


class TokenCipher:
    """Fernet-based symmetric cipher for encrypting and decrypting tokens.

    Wraps the ``cryptography.fernet.Fernet`` primitive so that the rest of the
    codebase works with plain strings and never touches raw bytes or key
    management directly. A single instance can be shared across the process
    because Fernet itself is thread-safe and stateless after initialisation.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialise the cipher from the application Fernet key.

        Reads ``FERNET_KEY`` from *settings* (or the global singleton),
        validates it, and builds the internal ``Fernet`` instance.

        Args:
            settings: Optional settings override; defaults to ``get_settings()``.

        Raises:
            CryptoConfigError: If the key is missing or malformed.
        """
        settings = settings or get_settings()
        key = settings.fernet_key.get_secret_value().strip()
        if not key:
            raise CryptoConfigError(
                "FERNET_KEY is not configured. Generate one and set it in the environment.",
            )
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise CryptoConfigError(
                "FERNET_KEY is not a valid Fernet key (must be 32 url-safe base64 bytes).",
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext string and return the ciphertext as a string.

        Args:
            plaintext: The value to encrypt (e.g. an OAuth access token).

        Returns:
            A Fernet-encoded ciphertext string safe for database storage.
        """
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a previously encrypted ciphertext back to plaintext.

        Args:
            ciphertext: A Fernet-encoded string retrieved from storage.

        Returns:
            The original plaintext value.

        Raises:
            CryptoDecryptError: If the ciphertext is invalid or was encrypted
                with a different key.
        """
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise CryptoDecryptError(
                "Stored ciphertext could not be decrypted with the current FERNET_KEY.",
            ) from exc
