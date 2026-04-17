"""Tests for JWT decoding and CurrentUser extraction.

Validates that AuthService correctly decodes tokens issued by Django simplejwt,
rejects expired or tampered tokens, and builds a proper CurrentUser dataclass.
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest

from src.common.exceptions import AuthenticationError
from src.services.auth_service import AuthService, CurrentUser
from tests.conftest import make_test_jwt


class TestDecodeValidToken:
    async def test_decode_valid_token(self, test_settings):
        auth = AuthService(settings=test_settings)
        token = make_test_jwt(test_settings, user_id=7, username="alice")

        claims = auth.decode_token(token)

        assert claims["user_id"] == 7
        assert claims["username"] == "alice"

    async def test_decode_includes_standard_claims(self, test_settings):
        auth = AuthService(settings=test_settings)
        token = make_test_jwt(test_settings, user_id=1)

        claims = auth.decode_token(token)

        assert "iat" in claims
        assert "exp" in claims


class TestDecodeRejections:
    async def test_decode_expired_token_raises(self, test_settings):
        """An expired JWT should raise AuthenticationError, not pass silently."""
        auth = AuthService(settings=test_settings)
        expired_payload = {
            "user_id": 1,
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,  # expired an hour ago
            "aud": test_settings.jwt_audience,
        }
        token = pyjwt.encode(
            expired_payload,
            test_settings.jwt_secret.get_secret_value(),
            algorithm="HS256",
        )

        with pytest.raises(AuthenticationError, match="expired"):
            auth.decode_token(token)

    async def test_decode_invalid_signature_raises(self, test_settings):
        """A token signed with a different key must be rejected."""
        auth = AuthService(settings=test_settings)
        token = pyjwt.encode(
            {"user_id": 1, "exp": int(time.time()) + 3600, "aud": test_settings.jwt_audience},
            "wrong-secret-key-not-the-real-one!!",
            algorithm="HS256",
        )

        with pytest.raises(AuthenticationError, match="signature"):
            auth.decode_token(token)

    async def test_decode_missing_user_id_raises(self, test_settings):
        """current_user_from_token must reject tokens without user_id."""
        auth = AuthService(settings=test_settings)
        # Valid signature but no user_id claim
        token = pyjwt.encode(
            {"exp": int(time.time()) + 3600, "aud": test_settings.jwt_audience},
            test_settings.jwt_secret.get_secret_value(),
            algorithm="HS256",
        )

        with pytest.raises(AuthenticationError, match="user_id"):
            auth.current_user_from_token(token)


class TestCurrentUserFromToken:
    async def test_current_user_from_token_success(self, test_settings):
        auth = AuthService(settings=test_settings)
        token = make_test_jwt(
            test_settings, user_id=42, username="bob", email="bob@example.com"
        )

        user = auth.current_user_from_token(token)

        assert isinstance(user, CurrentUser)
        assert user.id == 42
        assert user.username == "bob"
        assert user.email == "bob@example.com"

    async def test_current_user_optional_fields_absent(self, test_settings):
        """username and email should be None when claims are missing."""
        auth = AuthService(settings=test_settings)
        token = make_test_jwt(test_settings, user_id=1)

        user = auth.current_user_from_token(token)

        assert user.username is None
        assert user.email is None
