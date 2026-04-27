"""Shared test fixtures for the entire test suite.

Provides test settings with dummy secrets, mock git provider, JWT helpers,
and sample file-system fixtures. None of these fixtures talk to a real
database or external API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from src.config.settings import Settings
from src.db.models.project import GitProviderKind
from src.integrations.git.provider import (
    ClonedRepo,
    GitProvider,
    PullRequestInfo,
    RepoCoordinates,
    WorkflowRunSummary,
)


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


class MockGitProvider(GitProvider):
    """In-memory git provider that creates empty directories instead of cloning."""

    kind = GitProviderKind.GITHUB

    def __init__(self) -> None:
        self.cloned: list[RepoCoordinates] = []
        self.pushed: list[str] = []
        self.prs_created: list[dict] = []

    def parse_repo_url(self, url: str) -> RepoCoordinates:
        """Extract owner/name from a GitHub URL."""
        parts = url.rstrip("/").split("/")
        return RepoCoordinates(owner=parts[-2], name=parts[-1])

    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        """Return a fake GitHub OAuth URL."""
        return f"https://github.com/login/oauth/authorize?state={state}"

    async def exchange_code_for_token(self, *, code: str, redirect_uri: str) -> str:
        """Return a fake OAuth token."""
        return "gho_mock_token"

    async def clone(
        self,
        *,
        coordinates: RepoCoordinates,
        token: str,
        branch: str,
        destination: Path,
        depth: int = 1,
    ) -> ClonedRepo:
        """Create a minimal directory structure instead of a real git clone."""
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "README.md").write_text("# Mock repo\n")
        self.cloned.append(coordinates)
        return ClonedRepo(
            coordinates=coordinates,
            local_path=destination,
            branch=branch,
            head_commit="abc1234",
        )

    async def push_branch(self, *, repo_path: Path, branch: str, token: str) -> None:
        """Record the push without actually pushing."""
        self.pushed.append(branch)

    async def create_pull_request(
        self,
        *,
        coordinates: RepoCoordinates,
        token: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> PullRequestInfo:
        """Return a fake PR without hitting GitHub."""
        self.prs_created.append({"title": title, "head": head, "base": base})
        return PullRequestInfo(
            number=42,
            url=f"https://github.com/{coordinates.full_name}/pull/42",
            head_branch=head,
            base_branch=base,
        )

    async def fetch_workflow_run_logs(
        self, *, coordinates: RepoCoordinates, token: str, run_id: int
    ) -> str:
        """Return fake CI logs."""
        return "mock CI logs"

    async def revoke_token(self, *, token: str) -> None:
        """No-op for test purposes."""


@pytest.fixture
def mock_git_provider() -> MockGitProvider:
    """Provide a mock git provider that works entirely in memory."""
    return MockGitProvider()
