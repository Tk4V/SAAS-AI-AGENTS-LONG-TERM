"""HTTP views for the credentials-domain OAuth flow.

Mounted under ``/credentials/oauth/{provider}`` so the URL space stays
distinct from the legacy ``/auth/oauth/{provider}`` endpoints powered by
``services.oauth_service``. Both endpoints can run side by side until step
3 retires the legacy stack.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from fastapi.responses import RedirectResponse

from src.api.dependencies import CurrentUserDep, OAuthCredentialServiceDep
from src.api.schemas.auth_schemas import OAuthStartResponse
from src.db.models.project import ProviderKind
from src.utils.exceptions import AppError

router = APIRouter(prefix="/credentials/oauth", tags=["credentials"])


class CredentialOAuthView:
    """Authorize and callback endpoints for the unified credentials store."""

    @staticmethod
    @router.get("/{provider}/authorize", response_model=OAuthStartResponse)
    async def start(
        provider: ProviderKind,
        user: CurrentUserDep,
        service: OAuthCredentialServiceDep,
    ) -> OAuthStartResponse:
        """Return the provider's authorize URL with a signed state token."""
        url = service.start_flow(user_id=user.id, provider=provider)
        return OAuthStartResponse(provider=provider, authorization_url=url)

    @staticmethod
    @router.get("/{provider}/callback", include_in_schema=False)
    async def callback(
        provider: ProviderKind,
        service: OAuthCredentialServiceDep,
        code: str = Query(...),
        state: str = Query(...),
    ) -> RedirectResponse:
        """Exchange ``code`` for tokens and persist them as a credential."""
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
