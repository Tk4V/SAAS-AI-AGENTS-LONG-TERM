"""Git command-line operations against GitHub repositories.

This is *not* an OAuth concern. Cloning, pushing, and branch manipulation use
gitpython under the hood and only need a token at the moment of running the
command. Splitting these out from `client.py` keeps each file focused: REST
calls in one place, subprocess git calls in another.

Tokens are passed in per-call, not stored on the instance. Short-lived
credentials should not linger in process memory longer than needed.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from git import GitCommandError, Repo

from src.integrations._shared.exceptions import ProviderApiError


@dataclass(frozen=True, slots=True)
class RepoCoordinates:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True)
class ClonedRepo:
    coordinates: RepoCoordinates
    local_path: Path
    branch: str
    head_commit: str


_URL_PATTERNS = (
    re.compile(r"^https?://(?:[^@]+@)?github\.com/(?P<owner>[^/]+)/(?P<name>[^/.]+?)(?:\.git)?/?$"),
    re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<name>[^/.]+?)(?:\.git)?$"),
)


class GitHubGitOps:
    @staticmethod
    def parse_repo_url(url: str) -> RepoCoordinates:
        for pattern in _URL_PATTERNS:
            match = pattern.match(url.strip())
            if match:
                return RepoCoordinates(
                    owner=match.group("owner"),
                    name=match.group("name"),
                )
        raise ProviderApiError(
            f"URL {url!r} does not look like a GitHub repository."
        )

    @staticmethod
    async def clone(
        *,
        coordinates: RepoCoordinates,
        token: str,
        branch: str,
        destination: Path,
        depth: int = 1,
    ) -> ClonedRepo:
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)

        url = f"https://x-access-token:{token}@github.com/{coordinates.full_name}.git"

        def _clone() -> Repo:
            return Repo.clone_from(
                url,
                to_path=str(destination),
                branch=branch,
                depth=depth,
                single_branch=True,
            )

        try:
            repo = await asyncio.to_thread(_clone)
        except GitCommandError as exc:
            raise ProviderApiError(
                f"git clone of {coordinates.full_name} failed.",
                body=exc.stderr,
            ) from exc

        return ClonedRepo(
            coordinates=coordinates,
            local_path=destination,
            branch=branch,
            head_commit=repo.head.commit.hexsha,
        )

    @staticmethod
    async def push_branch(
        *,
        repo_path: Path,
        branch: str,
        token: str,
    ) -> None:
        def _push() -> None:
            repo = Repo(str(repo_path))
            origin = repo.remote("origin")
            coords = GitHubGitOps.parse_repo_url(next(origin.urls))
            authed_url = (
                f"https://x-access-token:{token}@github.com/{coords.full_name}.git"
            )
            with origin.config_writer as cw:
                cw.set("url", authed_url)
            origin.push(refspec=f"{branch}:{branch}")

        try:
            await asyncio.to_thread(_push)
        except GitCommandError as exc:
            raise ProviderApiError(
                f"git push of {branch} failed.",
                body=exc.stderr,
            ) from exc
