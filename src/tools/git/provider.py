"""Abstract git hosting provider used by every agent that touches a repository.

Agents talk to repositories through the methods on `GitProvider`. The concrete
implementation in `providers/` knows about the vendor specifics (GitHub today,
GitLab or Bitbucket later). Adding a new vendor means writing one class and
registering it with `GitProviderFactory`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from src.db.models.project import GitProviderKind


@dataclass(frozen=True, slots=True)
class RepoCoordinates:
    """The minimum information needed to identify a repository."""

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


@dataclass(frozen=True, slots=True)
class PullRequestInfo:
    number: int
    url: str
    head_branch: str
    base_branch: str


class GitProvider(ABC):
    """Vendor-neutral interface every git provider must implement."""

    kind: GitProviderKind

    @abstractmethod
    def parse_repo_url(self, url: str) -> RepoCoordinates:
        """Extract owner/name from a hosting URL."""

    @abstractmethod
    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        """Build the OAuth consent URL the user is redirected to."""

    @abstractmethod
    async def exchange_code_for_token(
        self, *, code: str, redirect_uri: str
    ) -> str:
        """Exchange the OAuth callback code for a long-lived access token."""

    @abstractmethod
    async def clone(
        self,
        *,
        coordinates: RepoCoordinates,
        token: str,
        branch: str,
        destination: Path,
        depth: int = 1,
    ) -> ClonedRepo:
        """Shallow-clone the repository into `destination` and return metadata."""

    @abstractmethod
    async def push_branch(
        self,
        *,
        repo_path: Path,
        branch: str,
        token: str,
    ) -> None:
        """Push the named branch to the remote, using token-based auth."""

    @abstractmethod
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
        """Open a PR and return its number plus URL."""

    @abstractmethod
    async def find_open_pr(
        self,
        *,
        coordinates: RepoCoordinates,
        token: str,
        head: str,
    ) -> PullRequestInfo | None:
        """Check if an open PR already exists for the given head branch."""

    @abstractmethod
    async def fetch_workflow_run_logs(
        self,
        *,
        coordinates: RepoCoordinates,
        token: str,
        run_id: int,
    ) -> str:
        """Download the CI logs for a workflow run as a single text blob."""

    @abstractmethod
    async def revoke_token(self, *, token: str) -> None:
        """Revoke an access token at the provider, not just locally."""
