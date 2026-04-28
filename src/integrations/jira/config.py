"""Jira (Atlassian) OAuth 2.0 provider declaration.

Atlassian uses OAuth 2.0 3LO (three-legged OAuth). Tokens expire in ~1 hour
and rotate on every refresh — both the access token and the refresh token are
replaced. TokenResolver.resolve() handles the refresh cycle transparently.

`offline_access` scope is required to receive a refresh token. Without it
Atlassian only issues a short-lived access token with no way to renew it.

`audience=api.atlassian.com` and `prompt=consent` are sent as extra
authorization parameters because Atlassian requires them for 3LO apps to
receive a refresh token on the first authorization.
"""

from __future__ import annotations

from src.integrations._shared.config import OAuthProviderConfig
from src.integrations._shared.kinds import IntegrationCategory, IntegrationKind
from src.integrations.jira.compliance import install_jira_compliance

JIRA = OAuthProviderConfig(
    kind=IntegrationKind.JIRA,
    category=IntegrationCategory.TRACKING,
    display_name="Jira",
    client_id_setting="jira_oauth_client_id",
    client_secret_setting="jira_oauth_client_secret",
    authorize_url="https://auth.atlassian.com/authorize",
    token_url="https://auth.atlassian.com/oauth/token",
    # Atlassian has no RFC 7009 revocation endpoint; the credential is deleted
    # from our DB and the access token expires naturally within ~1 hour.
    revoke_url=None,
    userinfo_url="https://api.atlassian.com/me",
    default_scopes=("read:jira-user", "read:jira-work", "write:jira-work", "offline_access"),
    scope_separator=" ",
    use_pkce=True,
    token_endpoint_auth_method="client_secret_post",
    refresh_supported=True,
    extra_authorize_params={
        "audience": "api.atlassian.com",
        "prompt": "consent",  # Forces Atlassian to always show consent + issue refresh token
    },
    api_base_url="https://api.atlassian.com",
    compliance_installer=install_jira_compliance,
)
