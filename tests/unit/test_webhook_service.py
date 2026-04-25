"""Tests for WebhookService signature verification and branch parsing."""

from __future__ import annotations

import hashlib
import hmac
import re

from src.services.webhook_service import WebhookService, _BRANCH_PATTERN


class TestSignatureVerification:
    """Verify HMAC-SHA256 signature checks against the configured webhook secret."""

    async def test_verify_valid_signature(self, test_settings) -> None:
        service = WebhookService(settings=test_settings)
        payload = b'{"action": "completed"}'
        secret = test_settings.github_webhook_secret.get_secret_value()
        sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        assert service.verify_signature(payload=payload, signature=sig) is True

    async def test_verify_invalid_signature_returns_false(self, test_settings) -> None:
        service = WebhookService(settings=test_settings)
        payload = b'{"action": "completed"}'

        assert service.verify_signature(payload=payload, signature="sha256=deadbeef") is False

    async def test_verify_empty_payload(self, test_settings) -> None:
        service = WebhookService(settings=test_settings)
        payload = b""
        secret = test_settings.github_webhook_secret.get_secret_value()
        sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        assert service.verify_signature(payload=payload, signature=sig) is True


class TestBranchParsing:
    """Verify regex extraction of task ID prefix from clyde branch names."""

    async def test_branch_parsing_extracts_task_prefix(self) -> None:
        match = _BRANCH_PATTERN.match("clyde/abcd1234/my-repo")

        assert match is not None
        assert match.group("prefix") == "abcd1234"
        assert match.group("repo") == "my-repo"

    async def test_branch_parsing_handles_nested_repo_name(self) -> None:
        match = _BRANCH_PATTERN.match("clyde/12345678/org/repo")

        assert match is not None
        assert match.group("prefix") == "12345678"
        assert match.group("repo") == "org/repo"

    async def test_branch_parsing_rejects_non_clyde_branches(self) -> None:
        assert _BRANCH_PATTERN.match("feature/my-feature") is None
        assert _BRANCH_PATTERN.match("main") is None
        assert _BRANCH_PATTERN.match("") is None

    async def test_branch_parsing_rejects_short_prefix(self) -> None:
        assert _BRANCH_PATTERN.match("clyde/abc/repo") is None
        assert _BRANCH_PATTERN.match("clyde/abcdefgh/repo") is None
