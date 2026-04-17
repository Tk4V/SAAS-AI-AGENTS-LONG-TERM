"""Tests for the RepoScanner file filtering and priority logic.

Uses a temporary directory with realistic project structure (created by the
sample_repo fixture) to verify that excluded dirs, binaries, and lockfiles
are skipped, that priority files appear first, and that limits are respected.
"""

from __future__ import annotations

from pathlib import Path

from src.agents.development_team.tech_lead.repo_scanner import RepoScanner


class TestRepoScanner:
    async def test_excludes_git_directory(self, sample_repo: Path):
        """The .git directory should never appear in scan results."""
        scanner = RepoScanner()
        insight = scanner.scan(name="test", root=sample_repo)

        all_paths = [s.path for s in insight.snippets]
        tree_entries = insight.file_tree

        # No snippet should come from .git/
        assert not any(".git" in p for p in all_paths), f"Found .git in snippets: {all_paths}"
        # The tree should also exclude .git/
        assert not any(entry.startswith(".git/") for entry in tree_entries)

    async def test_excludes_binary_files(self, sample_repo: Path):
        """Binary files (.png, .exe, etc.) should be skipped."""
        scanner = RepoScanner()
        insight = scanner.scan(name="test", root=sample_repo)

        snippet_paths = [s.path for s in insight.snippets]
        assert not any(p.endswith(".png") for p in snippet_paths)

    async def test_excludes_lockfiles(self, sample_repo: Path):
        """Lockfiles (poetry.lock, etc.) should be excluded from snippets."""
        scanner = RepoScanner()
        insight = scanner.scan(name="test", root=sample_repo)

        snippet_paths = [s.path for s in insight.snippets]
        assert not any("poetry.lock" in p for p in snippet_paths)

    async def test_excludes_node_modules(self, sample_repo: Path):
        """The node_modules directory should be completely excluded."""
        scanner = RepoScanner()
        insight = scanner.scan(name="test", root=sample_repo)

        snippet_paths = [s.path for s in insight.snippets]
        assert not any("node_modules" in p for p in snippet_paths)

    async def test_respects_max_files_limit(self, sample_repo: Path):
        """When max_files is set low, we should not exceed it."""
        scanner = RepoScanner(max_files=2)
        insight = scanner.scan(name="test", root=sample_repo)

        assert len(insight.snippets) <= 2

    async def test_priority_files_come_first(self, sample_repo: Path):
        """README and pyproject.toml should appear before other files."""
        scanner = RepoScanner()
        insight = scanner.scan(name="test", root=sample_repo)

        if len(insight.snippets) < 2:
            # Safety check: the fixture should produce at least 2 priority files
            return

        first_two = [s.path for s in insight.snippets[:2]]
        assert "README.md" in first_two
        assert "pyproject.toml" in first_two

    async def test_returns_file_tree(self, sample_repo: Path):
        """The scan should produce a non-empty file tree."""
        scanner = RepoScanner()
        insight = scanner.scan(name="test", root=sample_repo)

        assert len(insight.file_tree) > 0
        # The tree should include our source directory
        assert any("src/" in entry for entry in insight.file_tree)
