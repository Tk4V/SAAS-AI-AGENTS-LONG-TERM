"""HTTP endpoints for third-party OAuth integrations.

`start` is called by the frontend with the user's JWT and returns the GitHub
authorization URL. `callback` is called by GitHub with no JWT — identity is
recovered from the signed `state` token. After we exchange the code for an
access token, we redirect the browser back to the configured frontend URL
with a status query parameter so the SPA can render a friendly result.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from fastapi.responses import RedirectResponse

from src.api.deps import CurrentUserDep, OAuthServiceDep
from src.api.schemas.auth_schemas import (
    GitRepoItem,
    GitRepoList,
    IntegrationRead,
    IntegrationsList,
    OAuthStartResponse,
)
from src.common.exceptions import AppError
from src.config import get_settings
from src.db.models.project import GitProviderKind

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/oauth/{provider}/start", response_model=OAuthStartResponse)
async def oauth_start(
    provider: GitProviderKind,
    user: CurrentUserDep,
    service: OAuthServiceDep,
) -> OAuthStartResponse:
    url = service.start_flow(user_id=user.id, provider=provider)
    return OAuthStartResponse(provider=provider, authorization_url=url)


@router.get("/oauth/{provider}/callback", include_in_schema=False)
async def oauth_callback(
    provider: GitProviderKind,
    service: OAuthServiceDep,
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    settings = get_settings()
    base = settings.frontend_redirect_url.rstrip("?&")
    separator = "&" if ("?" in base) else "?"

    try:
        await service.handle_callback(provider=provider, code=code, state=state)
        target = (
            f"{base}{separator}integration={provider.value}&status=ok"
        )
    except AppError as exc:
        target = (
            f"{base}{separator}integration={provider.value}"
            f"&status=error&code={exc.code}"
        )
    return RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)


@router.get("/integrations", response_model=IntegrationsList)
async def list_integrations(
    user: CurrentUserDep,
    service: OAuthServiceDep,
) -> IntegrationsList:
    credentials = await service.list_for_user(user_id=user.id)
    return IntegrationsList(
        items=[IntegrationRead.from_orm(credential) for credential in credentials]
    )


@router.delete(
    "/integrations/{provider}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_integration(
    provider: GitProviderKind,
    user: CurrentUserDep,
    service: OAuthServiceDep,
) -> None:
    await service.revoke(user_id=user.id, provider=provider)


@router.get("/integrations/{provider}/repos", response_model=GitRepoList)
async def list_repos(
    provider: GitProviderKind,
    user: CurrentUserDep,
    service: OAuthServiceDep,
) -> GitRepoList:
    """List repositories the user has access to on the given provider."""
    repos = await service.list_repos(user_id=user.id, provider=provider)
    return GitRepoList(items=[GitRepoItem(**r) for r in repos])
