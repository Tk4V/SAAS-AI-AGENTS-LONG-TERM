"""HTTP view for the authenticated user's GitHub data.

Thin wrapper around :class:`GitHubApiClient` so the frontend can pull the
same repository list the agent stack uses, without duplicating GitHub-API
knowledge in the route layer. The user's stored OAuth token is resolved
by the existing ``OAuthTokenProvider`` — there is no per-request token
plumbing here.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.api.dependencies import CurrentUserDep, OAuthTokenProviderDep
from src.api.schemas.github_schemas import GitHubRepoRead, GitHubReposList
from src.integrations.github import GitHubApiClient

router = APIRouter(prefix="/github", tags=["GitHub"])


class GitHubView:
    """Endpoints backed by the caller's GitHub OAuth credential."""

    @staticmethod
    @router.get(
        "/repos",
        response_model=GitHubReposList,
        summary="List the caller's GitHub repositories",
        description=(
            "Returns every repository visible to the user's connected "
            "GitHub OAuth credential, sorted by most-recently-updated first. "
            "Requires a GitHub OAuth credential — 404 otherwise."
        ),
    )
    async def list_repos(
        user: CurrentUserDep,
        token_provider: OAuthTokenProviderDep,
    ) -> GitHubReposList:
        client = GitHubApiClient(user_id=user.id, token_provider=token_provider)
        try:
            repos = await client.list_repos()
        finally:
            await client.aclose()
        return GitHubReposList(items=[GitHubRepoRead(**repo) for repo in repos])
