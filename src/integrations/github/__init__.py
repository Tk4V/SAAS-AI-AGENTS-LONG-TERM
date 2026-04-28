"""GitHub integration: OAuth config + REST API client + git operations + revoker.

Whatever the rest of the codebase needs from "the GitHub integration"
re-exports here. Internal files in this folder are implementation detail.
"""

from src.integrations.github.client import GitHubApiClient
from src.integrations.github.config import GITHUB
from src.integrations.github.git_ops import (
    ClonedRepo,
    GitHubGitOps,
    RepoCoordinates,
)
from src.integrations.github.revoker import revoke_github_token

__all__ = [
    "GITHUB",
    "ClonedRepo",
    "GitHubApiClient",
    "GitHubGitOps",
    "RepoCoordinates",
    "revoke_github_token",
]
