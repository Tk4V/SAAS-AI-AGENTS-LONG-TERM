"""GitHub OAuth provider declaration.

Pure data. The shape of the constant tells the framework everything it needs
to drive the OAuth dance: where to redirect the user, what scopes to ask
for, where to swap the code for a token, how the provider authenticates
the token request, and which custom revoker to use because GitHub does not
implement RFC 7009.

No `compliance_installer`: Authlib's default response parser already handles
both JSON and form-urlencoded token responses, so GitHub does not need any
quirk hook.

Anything dynamic (cloning a repo, opening a PR, listing branches) lives in
`client.py` and `git_ops.py`, not here.
"""

from __future__ import annotations

from src.integrations._shared.config import OAuthProviderConfig
from src.integrations._shared.kinds import IntegrationCategory, IntegrationKind
from src.integrations.github.revoker import revoke_github_token

GITHUB = OAuthProviderConfig(
    kind=IntegrationKind.GITHUB,
    category=IntegrationCategory.VCS,
    display_name="GitHub",
    client_id_setting="github_oauth_client_id",
    client_secret_setting="github_oauth_client_secret",
    authorize_url="https://github.com/login/oauth/authorize",
    token_url="https://github.com/login/oauth/access_token",
    # GitHub uses a non-standard revocation flow handled by `revoke_github_token`.
    # Leaving revoke_url=None makes OAuthAdapter fall through to custom_revoker.
    revoke_url=None,
    default_scopes=("repo", "workflow"),
    scope_separator=",",  # GitHub joins scopes with commas, not spaces
    use_pkce=False,  # GitHub OAuth App rejects PKCE (only GitHub Apps support it)
    refresh_supported=False,  # OAuth App tokens do not rotate
    token_endpoint_auth_method="client_secret_post",
    api_base_url="https://api.github.com",
    custom_revoker=revoke_github_token,
)
