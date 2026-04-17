"""Tests for WebhookService signature verification and branch parsing.

Covers HMAC-SHA256 verification against the configured webhook secret, rejection
of invalid signatures, and the regex that extracts a task ID prefix from branch
names created by the Release Manager.
"""

from __future__ import annotations

import hashlib
import hmac
import re

from src.services.webhook_service import WebhookService, _BRANCH_PATTERN


class TestSignatureVerification:
    async def test_verify_valid_signature(self, test_settings):
        """A correctly signed payload should pass verification."""
        service = WebhookService(settings=test_settings)
        payload = b'{"action": "completed"}'
        secret = test_settings.github_webhook_secret.get_secret_value()
        sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        assert service.verify_signature(payload=payload, signature=sig) is True

    async def test_verify_invalid_signature_returns_false(self, test_settings):
        """A tampered signature should be rejected."""
        service = WebhookService(settings=test_settings)
        payload = b'{"action": "completed"}'

        assert service.verify_signature(payload=payload, signature="sha256=deadbeef") is False

    async def test_verify_empty_payload(self, test_settings):
        """Even an empty payload should work with the right signature."""
        service = WebhookService(settings=test_settings)
        payload = b""
        secret = test_settings.github_webhook_secret.get_secret_value()
        sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        assert service.verify_signature(payload=payload, signature=sig) is True


class TestBranchParsing:
    async def test_branch_parsing_extracts_task_prefix(self):
        """Branch names like clyde/abcd1234/my-repo should parse correctly."""
        match = _BRANCH_PATTERN.match("clyde/abcd1234/my-repo")

        assert match is not None
        assert match.group("prefix") == "abcd1234"
        assert match.group("repo") == "my-repo"

    async def test_branch_parsing_handles_nested_repo_name(self):
        """Repo names with slashes should be captured entirely."""
        match = _BRANCH_PATTERN.match("clyde/12345678/org/repo")

        assert match is not None
        assert match.group("prefix") == "12345678"
        assert match.group("repo") == "org/repo"

    async def test_branch_parsing_rejects_non_clyde_branches(self):
        """Branches not starting with clyde/ should not match."""
        assert _BRANCH_PATTERN.match("feature/my-feature") is None
        assert _BRANCH_PATTERN.match("main") is None
        assert _BRANCH_PATTERN.match("") is None

    async def test_branch_parsing_rejects_short_prefix(self):
        """The prefix must be exactly 8 hex chars."""
        assert _BRANCH_PATTERN.match("clyde/abc/repo") is None
        assert _BRANCH_PATTERN.match("clyde/abcdefgh/repo") is None  # letters not hex
