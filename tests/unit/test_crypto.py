"""Tests for the Fernet-based TokenCipher."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from src.utils.crypto import CryptoConfigError, CryptoDecryptError, TokenCipher
from tests.conftest import make_settings_with_fernet_key


class TestTokenCipher:
    """Verify encrypt/decrypt round-trips and key validation."""

    async def test_encrypt_decrypt_roundtrip(self, test_settings) -> None:
        cipher = TokenCipher(settings=test_settings)
        secret = "gho_super_secret_oauth_token_12345"
        encrypted = cipher.encrypt(secret)
        decrypted = cipher.decrypt(encrypted)
        assert decrypted == secret
        assert secret not in encrypted

    async def test_decrypt_with_wrong_key_raises(self, test_settings) -> None:
        cipher_a = TokenCipher(settings=test_settings)
        encrypted = cipher_a.encrypt("some token")
        other_settings = make_settings_with_fernet_key(Fernet.generate_key().decode())
        cipher_b = TokenCipher(settings=other_settings)
        with pytest.raises(CryptoDecryptError):
            cipher_b.decrypt(encrypted)

    async def test_empty_key_raises_config_error(self) -> None:
        settings = make_settings_with_fernet_key("")
        with pytest.raises(CryptoConfigError, match="not configured"):
            TokenCipher(settings=settings)

    async def test_invalid_key_raises_config_error(self) -> None:
        settings = make_settings_with_fernet_key("not-a-valid-fernet-key")
        with pytest.raises(CryptoConfigError, match="not a valid"):
            TokenCipher(settings=settings)
