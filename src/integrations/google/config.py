"""Google OAuth provider declaration.

Google issues short-lived (1h) access tokens and a long-lived refresh token.
Refresh tokens are returned only when ``access_type=offline`` plus
``prompt=consent`` are sent on the authorize request, otherwise the second
authorization yields no refresh token and the user is locked out after the
first hour.

PKCE is supported and enabled. Default scopes ask only for the bare minimum
profile + openid identification; downstream callers attach the API-specific
scopes they need at authorization time when broader access is required.
"""

from __future__ import annotations

from src.integrations._shared.config import OAuthProviderConfig
from src.integrations._shared.kinds import IntegrationCategory, IntegrationKind

GOOGLE = OAuthProviderConfig(
    kind=IntegrationKind.GOOGLE,
    category=IntegrationCategory.IDENTITY,
    display_name="Google",
    client_id_setting="google_oauth_client_id",
    client_secret_setting="google_oauth_client_secret",
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    revoke_url="https://oauth2.googleapis.com/revoke",
    userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
    default_scopes=("openid", "email", "profile"),
    scope_separator=" ",
    use_pkce=True,
    token_endpoint_auth_method="client_secret_post",
    refresh_supported=True,
    extra_authorize_params={
        "access_type": "offline",
        "prompt": "consent",
    },
    api_base_url="https://www.googleapis.com",
)
