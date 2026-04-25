"""HTTP views for OAuth integrations.

`start` is called by the frontend with the user's JWT and returns the GitHub
authorization URL. `callback` is called by GitHub with no JWT — identity is
recovered from the signed `state` token. After exchanging the code for an
access token, the browser is redirected back to the frontend.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from fastapi.responses import RedirectResponse

from src.api.dependencies import CurrentUserDep, OAuthServiceDep
from src.api.schemas.auth_schemas import (
    GitRepoItem,
    GitRepoList,
    IntegrationRead,
    IntegrationsList,
    OAuthStartResponse,
)
from src.utils.exceptions import AppError
from src.db.models.project import GitProviderKind

router = APIRouter(prefix="/auth", tags=["auth"])


class OAuthView:
    """OAuth flow endpoints: initiate, callback, list, and revoke."""

    @staticmethod
    @router.get("/oauth/{provider}/start", response_model=OAuthStartResponse)
    async def start(
        provider: GitProviderKind,
        user: CurrentUserDep,
        service: OAuthServiceDep,
    ) -> OAuthStartResponse:
        """Return the provider's authorization URL to redirect the user to."""
        url = service.start_flow(user_id=user.id, provider=provider)
        return OAuthStartResponse(provider=provider, authorization_url=url)

    @staticmethod
    @router.get("/oauth/{provider}/callback", include_in_schema=False)
    async def callback(
        provider: GitProviderKind,
        service: OAuthServiceDep,
        code: str = Query(...),
        state: str = Query(...),
    ) -> RedirectResponse:
        """Handle the OAuth callback from the provider."""
        try:
            await service.handle_callback(provider=provider, code=code, state=state)
            redirect_url = service.build_callback_redirect_url(
                provider=provider, success=True
            )
        except AppError as exc:
            redirect_url = service.build_callback_redirect_url(
                provider=provider, success=False, error_code=exc.code
            )

        return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)


class IntegrationView:
    """Manage connected integrations and list accessible repositories."""

    @staticmethod
    @router.get("/integrations", response_model=IntegrationsList)
    async def list_integrations(
        user: CurrentUserDep,
        service: OAuthServiceDep,
    ) -> IntegrationsList:
        """List all OAuth integrations connected by the current user."""
        credentials = await service.list_for_user(user_id=user.id)
        return IntegrationsList(
            items=[IntegrationRead.from_orm(credential) for credential in credentials]
        )

    @staticmethod
    @router.delete("/integrations/{provider}", status_code=status.HTTP_204_NO_CONTENT)
    async def revoke(
        provider: GitProviderKind,
        user: CurrentUserDep,
        service: OAuthServiceDep,
    ) -> None:
        """Revoke an OAuth integration and delete the stored token."""
        await service.revoke(user_id=user.id, provider=provider)

    @staticmethod
    @router.get("/integrations/{provider}/repos", response_model=GitRepoList)
    async def list_repos(
        provider: GitProviderKind,
        user: CurrentUserDep,
        service: OAuthServiceDep,
    ) -> GitRepoList:
        """List repositories the user has access to on the given provider."""
        repos = await service.list_repos(user_id=user.id, provider=provider)
        return GitRepoList(items=[GitRepoItem(**r) for r in repos])
