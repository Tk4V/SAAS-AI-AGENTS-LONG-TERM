"""Tests for the Fernet-based TokenCipher.

Covers encrypt/decrypt round-trips, cross-key decryption failures, and the
startup validation that rejects an empty FERNET_KEY.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from src.common.crypto import CryptoConfigError, CryptoDecryptError, TokenCipher
from src.config.settings import Settings


def _make_settings_with_key(key: str) -> Settings:
    """Build minimal settings with a custom fernet key."""
    return Settings(
        app_env="local",
        db_host="localhost",
        db_port=5432,
        db_name="clyde_test",
        db_user="clyde",
        db_password=SecretStr("clyde"),
        jwt_secret=SecretStr("test-jwt-secret-at-least-32-chars-long"),
        anthropic_api_key=SecretStr("sk-ant-test"),
        voyage_api_key=SecretStr("pa-test"),
        fernet_key=SecretStr(key),
        github_oauth_client_id=SecretStr("test-client-id"),
        github_oauth_client_secret=SecretStr("test-client-secret"),
        github_webhook_secret=SecretStr("test-webhook-secret"),
    )


class TestTokenCipher:
    async def test_encrypt_decrypt_roundtrip(self, test_settings):
        """Encrypting then decrypting should return the original plaintext."""
        cipher = TokenCipher(settings=test_settings)
        secret = "gho_super_secret_oauth_token_12345"

        encrypted = cipher.encrypt(secret)
        decrypted = cipher.decrypt(encrypted)

        assert decrypted == secret
        # Ciphertext should not contain the original plaintext
        assert secret not in encrypted

    async def test_decrypt_wrong_key_raises(self, test_settings):
        """Decrypting with a different Fernet key must raise CryptoDecryptError."""
        cipher_a = TokenCipher(settings=test_settings)
        encrypted = cipher_a.encrypt("some token")

        # Build a second cipher with a completely different key
        other_settings = _make_settings_with_key(Fernet.generate_key().decode())
        cipher_b = TokenCipher(settings=other_settings)

        with pytest.raises(CryptoDecryptError):
            cipher_b.decrypt(encrypted)

    async def test_empty_key_raises_config_error(self):
        """TokenCipher should refuse to initialise without a valid FERNET_KEY."""
        bad_settings = _make_settings_with_key("")

        with pytest.raises(CryptoConfigError, match="not configured"):
            TokenCipher(settings=bad_settings)

    async def test_invalid_key_raises_config_error(self):
        """A malformed Fernet key should raise CryptoConfigError at init time."""
        bad_settings = _make_settings_with_key("not-a-valid-fernet-key")

        with pytest.raises(CryptoConfigError, match="not a valid"):
            TokenCipher(settings=bad_settings)
