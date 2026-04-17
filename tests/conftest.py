"""Shared test fixtures for the entire test suite.

Provides test settings with dummy secrets, mock implementations of external
services (LLM, Git, Sandbox), JWT helpers, and sample file-system fixtures.
None of these fixtures talk to a real database or external API.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from src.config.settings import Settings
from src.db.models.project import GitProviderKind
from src.engine.state import PipelineEvent
from src.tools.git.provider import (
    ClonedRepo,
    GitProvider,
    PullRequestInfo,
    RepoCoordinates,
    WorkflowRunSummary,
)
from src.tools.llm.gateway import ChatMessage, ChatResponse, LLMGateway, TokenUsage


# ---------------------------------------------------------------------------
# 1. Test settings — in-memory, no .env file needed
# ---------------------------------------------------------------------------

@pytest.fixture
def test_settings() -> Settings:
    """Builds a Settings object with safe dummy values for every secret."""
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


# ---------------------------------------------------------------------------
# 2. Mock LLM gateway
# ---------------------------------------------------------------------------

class MockLLMGateway(LLMGateway):
    """Returns canned responses for testing agents without hitting Anthropic."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses or ["mock response"])
        self._call_index = 0
        self.calls: list[dict] = []  # record calls for assertions

    async def chat(
        self,
        *,
        role: str,
        system: str,
        messages: list[ChatMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        self.calls.append({
            "role": role,
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        text = self._responses[min(self._call_index, len(self._responses) - 1)]
        self._call_index += 1
        return ChatResponse(text=text, model="mock-model", usage=TokenUsage(0, 0))

    async def stream(
        self,
        *,
        role: str,
        system: str,
        messages: list[ChatMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        yield "mock stream"


@pytest.fixture
def mock_llm() -> MockLLMGateway:
    return MockLLMGateway()


# ---------------------------------------------------------------------------
# 3. Mock GitProvider
# ---------------------------------------------------------------------------

class MockGitProvider(GitProvider):
    """In-memory git provider that creates empty directories instead of cloning."""

    kind = GitProviderKind.GITHUB

    def __init__(self) -> None:
        self.cloned: list[RepoCoordinates] = []
        self.pushed: list[str] = []
        self.prs_created: list[dict] = []

    def parse_repo_url(self, url: str) -> RepoCoordinates:
        # Simple extraction: assume https://github.com/owner/name
        parts = url.rstrip("/").split("/")
        return RepoCoordinates(owner=parts[-2], name=parts[-1])

    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        return f"https://github.com/login/oauth/authorize?state={state}"

    async def exchange_code_for_token(self, *, code: str, redirect_uri: str) -> str:
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
        destination.mkdir(parents=True, exist_ok=True)
        # Create a minimal file so the scanner has something to find
        (destination / "README.md").write_text("# Mock repo\n")
        (destination / "pyproject.toml").write_text('[project]\nname = "mock"\n')
        self.cloned.append(coordinates)
        return ClonedRepo(
            coordinates=coordinates,
            local_path=destination,
            branch=branch,
            head_commit="abc1234",
        )

    async def push_branch(self, *, repo_path: Path, branch: str, token: str) -> None:
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
        return "mock CI logs"

    async def revoke_token(self, *, token: str) -> None:
        pass


@pytest.fixture
def mock_git_provider() -> MockGitProvider:
    return MockGitProvider()


# ---------------------------------------------------------------------------
# 4. JWT helper
# ---------------------------------------------------------------------------

def make_test_jwt(
    settings: Settings,
    user_id: int = 1,
    **extra_claims: Any,
) -> str:
    """Encode a JWT using the test settings secret, matching Django simplejwt layout."""
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


# ---------------------------------------------------------------------------
# 5. Sample repo on disk for scanner / chunker tests
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a realistic mini Python project structure for scanning tests."""
    root = tmp_path / "sample_project"
    root.mkdir()

    # Priority files
    (root / "README.md").write_text("# Sample Project\nA test fixture.\n")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n'
    )

    # Source files
    src = root / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "main.py").write_text(
        'def greet(name: str) -> str:\n    """Say hello."""\n    return f"Hello, {name}!"\n\n\n'
        "class Greeter:\n    def __init__(self, prefix: str) -> None:\n"
        "        self.prefix = prefix\n\n"
        "    def greet(self, name: str) -> str:\n"
        '        return f"{self.prefix} {name}"\n'
    )
    (src / "utils.py").write_text(
        "import os\n\n\ndef read_env(key: str) -> str:\n"
        '    return os.environ.get(key, "")\n'
    )

    # A binary file that should be excluded
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    # .git directory that should be excluded
    git_dir = root / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")

    # node_modules should be excluded
    nm = root / "node_modules"
    nm.mkdir()
    (nm / "leftpad.js").write_text("module.exports = function leftpad() {};")

    # A lockfile that should be excluded
    (root / "poetry.lock").write_text("# lock contents")

    return root
