"""Builds the right `GitProvider` instance for a given repository URL.

Today GitHub is the only supported provider, so the factory always returns
`GitHubProvider`. The class still exists so that adding GitLab in M2 means
registering a new branch in `for_url` rather than touching call sites.
"""

from __future__ import annotations

from src.utils.exceptions import ExternalServiceError
from src.config import Settings, get_settings
from src.db.models.project import GitProviderKind
from src.tools.custom_tools.git.git_provider import GitProvider
from src.tools.custom_tools.git.github_provider import GitHubProvider


class GitProviderFactory:
    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._cache: dict[GitProviderKind, GitProvider] = {}

    def for_kind(self, kind: GitProviderKind) -> GitProvider:
        if kind not in self._cache:
            self._cache[kind] = self._build(kind)
        return self._cache[kind]

    def for_url(self, url: str) -> GitProvider:
        kind = self._kind_for_url(url)
        return self.for_kind(kind)

    async def aclose(self) -> None:
        for provider in self._cache.values():
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()
        self._cache.clear()

    def _build(self, kind: GitProviderKind) -> GitProvider:
        if kind is GitProviderKind.GITHUB:
            return GitHubProvider(settings=self._settings)
        raise ExternalServiceError(
            f"No GitProvider implementation registered for {kind.value}.",
        )

    @staticmethod
    def _kind_for_url(url: str) -> GitProviderKind:
        lowered = url.lower()
        if "github.com" in lowered:
            return GitProviderKind.GITHUB
        raise ExternalServiceError(
            f"Cannot determine git provider from URL {url!r}.",
        )
