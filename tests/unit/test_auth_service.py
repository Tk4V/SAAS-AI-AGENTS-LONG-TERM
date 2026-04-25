"""Tests for JWT decoding and CurrentUser extraction."""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest

from src.services.auth_service import AuthService, CurrentUser
from src.utils.exceptions import AuthenticationError
from tests.conftest import make_test_jwt


class TestDecodeValidToken:
    """Verify that well-formed tokens are decoded correctly."""

    async def test_extracts_user_id(self, test_settings) -> None:
        auth = AuthService(settings=test_settings)
        token = make_test_jwt(test_settings, user_id=7, username="alice")
        claims = auth.decode_token(token)
        assert claims["user_id"] == 7
        assert claims["username"] == "alice"

    async def test_includes_standard_claims(self, test_settings) -> None:
        auth = AuthService(settings=test_settings)
        token = make_test_jwt(test_settings, user_id=1)
        claims = auth.decode_token(token)
        assert "iat" in claims
        assert "exp" in claims


class TestDecodeRejections:
    """Verify that invalid tokens raise AuthenticationError."""

    async def test_expired_token(self, test_settings) -> None:
        auth = AuthService(settings=test_settings)
        token = pyjwt.encode(
            {"user_id": 1, "iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600, "aud": test_settings.jwt_audience},
            test_settings.jwt_secret.get_secret_value(), algorithm="HS256",
        )
        with pytest.raises(AuthenticationError, match="expired"):
            auth.decode_token(token)

    async def test_invalid_signature(self, test_settings) -> None:
        auth = AuthService(settings=test_settings)
        token = pyjwt.encode(
            {"user_id": 1, "exp": int(time.time()) + 3600, "aud": test_settings.jwt_audience},
            "wrong-secret-key-not-the-real-one!!", algorithm="HS256",
        )
        with pytest.raises(AuthenticationError, match="signature"):
            auth.decode_token(token)

    async def test_missing_user_id(self, test_settings) -> None:
        auth = AuthService(settings=test_settings)
        token = pyjwt.encode(
            {"exp": int(time.time()) + 3600, "aud": test_settings.jwt_audience},
            test_settings.jwt_secret.get_secret_value(), algorithm="HS256",
        )
        with pytest.raises(AuthenticationError, match="user_id"):
            auth.current_user_from_token(token)


class TestCurrentUserFromToken:
    """Verify CurrentUser dataclass is built correctly from token claims."""

    async def test_all_fields_populated(self, test_settings) -> None:
        auth = AuthService(settings=test_settings)
        token = make_test_jwt(test_settings, user_id=42, username="bob", email="bob@example.com")
        user = auth.current_user_from_token(token)
        assert isinstance(user, CurrentUser)
        assert user.id == 42
        assert user.username == "bob"
        assert user.email == "bob@example.com"

    async def test_optional_fields_default_to_none(self, test_settings) -> None:
        auth = AuthService(settings=test_settings)
        token = make_test_jwt(test_settings, user_id=1)
        user = auth.current_user_from_token(token)
        assert user.username is None
        assert user.email is None

    async def test_string_user_id_is_converted_to_int(self, test_settings) -> None:
        """Django simplejwt may serialize user_id as a string."""
        auth = AuthService(settings=test_settings)
        token = make_test_jwt(test_settings, user_id="7")
        user = auth.current_user_from_token(token)
        assert user.id == 7
        assert isinstance(user.id, int)
