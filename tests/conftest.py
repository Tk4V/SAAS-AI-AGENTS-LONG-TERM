"""Shared test fixtures for the entire test suite.

Provides test settings with dummy secrets and JWT helpers. None of these
fixtures talk to a real database or external API.

Provider-level mocks (a fake GitHub API, a fake git_ops) live alongside the
tests that need them. Each provider folder will grow its own test helpers as
the integration suite fills in.
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from src.config.settings import Settings


@pytest.fixture
def test_settings() -> Settings:
    """Build a Settings object with safe dummy values for every secret."""
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
        fernet_key=SecretStr(Fernet.generate_key().decode()),
        github_oauth_client_id=SecretStr("test-client-id"),
        github_oauth_client_secret=SecretStr("test-client-secret"),
        github_webhook_secret=SecretStr("test-webhook-secret"),
    )


def make_settings_with_fernet_key(key: str) -> Settings:
    """Build Settings with a custom fernet key for crypto tests."""
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


def make_test_jwt(
    settings: Settings,
    user_id: int = 1,
    **extra_claims: Any,
) -> str:
    """Encode a JWT using the test settings secret."""
    import time

    import jwt as pyjwt

    now = int(time.time())
    payload: dict[str, Any] = {
        "user_id": user_id,
        "iat": now,
        "exp": now + 3600,
        "aud": settings.jwt_audience,
        **extra_claims,
    }
    return pyjwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
