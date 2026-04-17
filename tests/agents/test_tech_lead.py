"""Tests for the TechLeadAgent with fully mocked dependencies.

Exercises the agent's execute() method end-to-end using a MockLLMGateway
(returns valid JSON context), a MockGitProvider (clones into temp dirs),
and mock Database/TokenCipher. Verifies the returned state includes repos,
context, and events without touching any real service.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.development_team.tech_lead.agent import TechLeadAgent
from src.agents.development_team.tech_lead.repo_scanner import RepoScanner
from src.common.crypto import TokenCipher
from src.db.models.project import GitProviderKind
from src.tools.git.factory import GitProviderFactory
from src.tools.git.provider import ClonedRepo, RepoCoordinates
from tests.conftest import MockGitProvider, MockLLMGateway


def _make_mock_git_factory(provider: MockGitProvider) -> GitProviderFactory:
    """Wrap the mock provider in a factory-like object."""
    factory = MagicMock(spec=GitProviderFactory)
    factory.for_kind.return_value = provider
    return factory


def _make_mock_database(cipher: TokenCipher) -> Any:
    """Build a mock Database whose session_scope returns a mock credential."""
    from unittest.mock import AsyncMock

    # The credential object that UserOAuthCredentialRepository.get() returns
    fake_credential = MagicMock()
    fake_credential.token_encrypted = cipher.encrypt("gho_real_token_123")

    # Mock the repository's get() method
    mock_repo = AsyncMock()
    mock_repo.get.return_value = fake_credential

    # The session context manager
    mock_session = AsyncMock()

    # Build a mock database with session_scope
    mock_db = MagicMock()

    # We need to patch UserOAuthCredentialRepository inside the agent code.
    # The simplest approach: mock the entire _resolve_github_token method.
    return mock_db


class TestTechLeadAgent:
    async def test_execute_returns_repos_context_events(
        self, test_settings, mock_git_provider, tmp_path
    ):
        """The happy path: clone, scan, merge context, return state diff."""
        # The LLM should return valid JSON that becomes the context
        context_json = json.dumps({
            "summary": "A Python web app",
            "relevant_files": ["src/main.py"],
        })
        mock_llm = MockLLMGateway(responses=[context_json])
        cipher = TokenCipher(settings=test_settings)
        factory = _make_mock_git_factory(mock_git_provider)

        agent = TechLeadAgent(
            llm=mock_llm,
            git_factory=factory,
            cipher=cipher,
            scanner=RepoScanner(),
        )

        # Patch _resolve_github_token so we don't need a real DB
        agent._resolve_github_token = AsyncMock(return_value="gho_test_token")

        state = {
            "task_id": "test-task-001",
            "user_id": 1,
            "description": "Add a login page",
            "repos": [
                {
                    "url": "https://github.com/acme/webapp",
                    "default_branch": "main",
                }
            ],
        }

        result = await agent.execute(state)

        # Should return the three keys the pipeline expects
        assert "repos" in result
        assert "context" in result
        assert "events" in result

        # Repos should reflect what was cloned
        assert len(result["repos"]) == 1
        assert result["repos"][0]["name"] == "webapp"
        assert result["repos"][0]["head_commit"] == "abc1234"

        # Context should be the parsed JSON from the LLM
        assert result["context"]["summary"] == "A Python web app"

        # Events should include the tech_lead.context_built event
        assert len(result["events"]) == 1
        assert result["events"][0]["name"] == "tech_lead.context_built"
        assert result["events"][0]["agent"] == "tech_lead"

    async def test_execute_calls_llm_with_correct_role(
        self, test_settings, mock_git_provider
    ):
        """Verify the LLM is called with role='tech_lead'."""
        context_json = json.dumps({"summary": "test"})
        mock_llm = MockLLMGateway(responses=[context_json])
        cipher = TokenCipher(settings=test_settings)
        factory = _make_mock_git_factory(mock_git_provider)

        agent = TechLeadAgent(
            llm=mock_llm,
            git_factory=factory,
            cipher=cipher,
            scanner=RepoScanner(),
        )
        agent._resolve_github_token = AsyncMock(return_value="gho_test")

        state = {
            "task_id": "t-002",
            "user_id": 1,
            "description": "Refactor auth module",
            "repos": [{"url": "https://github.com/acme/api", "default_branch": "main"}],
        }

        await agent.execute(state)

        # The merger should have called the LLM once
        assert len(mock_llm.calls) == 1
        assert mock_llm.calls[0]["role"] == "tech_lead"

    async def test_execute_clones_multiple_repos(
        self, test_settings, mock_git_provider
    ):
        """When multiple repos are attached, all should be cloned and scanned."""
        context_json = json.dumps({"summary": "multi-repo project"})
        mock_llm = MockLLMGateway(responses=[context_json])
        cipher = TokenCipher(settings=test_settings)
        factory = _make_mock_git_factory(mock_git_provider)

        agent = TechLeadAgent(
            llm=mock_llm,
            git_factory=factory,
            cipher=cipher,
            scanner=RepoScanner(),
        )
        agent._resolve_github_token = AsyncMock(return_value="gho_test")

        state = {
            "task_id": "t-003",
            "user_id": 1,
            "description": "Sync frontend and backend",
            "repos": [
                {"url": "https://github.com/acme/frontend", "default_branch": "main"},
                {"url": "https://github.com/acme/backend", "default_branch": "develop"},
            ],
        }

        result = await agent.execute(state)

        assert len(result["repos"]) == 2
        assert result["repos"][0]["name"] == "frontend"
        assert result["repos"][1]["name"] == "backend"
        # The provider should have been asked to clone both
        assert len(mock_git_provider.cloned) == 2

    async def test_execute_raises_without_user_id(self, test_settings, mock_git_provider):
        """Missing user_id in state should raise PipelineError."""
        from src.common.exceptions import PipelineError

        mock_llm = MockLLMGateway()
        cipher = TokenCipher(settings=test_settings)
        factory = _make_mock_git_factory(mock_git_provider)

        agent = TechLeadAgent(
            llm=mock_llm,
            git_factory=factory,
            cipher=cipher,
        )

        state = {"task_id": "t-004", "description": "something", "repos": []}

        with pytest.raises(PipelineError, match="user_id"):
            await agent.execute(state)

    async def test_execute_raises_without_repos(self, test_settings, mock_git_provider):
        """Empty repos list should raise PipelineError."""
        from src.common.exceptions import PipelineError

        mock_llm = MockLLMGateway()
        cipher = TokenCipher(settings=test_settings)
        factory = _make_mock_git_factory(mock_git_provider)

        agent = TechLeadAgent(
            llm=mock_llm,
            git_factory=factory,
            cipher=cipher,
        )
        agent._resolve_github_token = AsyncMock(return_value="gho_test")

        state = {"task_id": "t-005", "user_id": 1, "description": "something", "repos": []}

        with pytest.raises(PipelineError, match="repositories"):
            await agent.execute(state)
