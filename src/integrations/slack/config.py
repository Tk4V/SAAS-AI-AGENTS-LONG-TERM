"""Slack OAuth provider declaration.

Uses the Slack user OAuth flow (``oauth/v2_user/authorize`` +
``oauth.v2.user.access``) to obtain an ``xoxp-`` user token.  This is
required because the official Slack MCP server (``mcp.slack.com/mcp``) rejects
bot tokens (``xoxb-``) with ``invalid_token_type`` — it only accepts tokens
issued by its own authorization server, which uses the user token endpoints.

Slack uses a non-standard OAuth response: ``200 OK`` with ``ok: false`` for
errors, hence the compliance hook. Token rotation is opt-in per app config —
when enabled Slack issues 12h-life tokens that rotate on refresh; we declare
``refresh_supported=True`` and the resolver will only attempt refresh when
an ``expires_at`` is present.

Required setup in the Slack app settings (api.slack.com/apps):
Add all scopes below under **User Token Scopes** (not Bot Token Scopes).
"""

from __future__ import annotations

from src.agent_tools.mcp.slack import slack_mcp_server
from src.integrations._shared.config import OAuthProviderConfig
from src.integrations._shared.kinds import IntegrationCategory, IntegrationKind
from src.integrations.slack.compliance import install_slack_compliance

SLACK = OAuthProviderConfig(
    kind=IntegrationKind.SLACK,
    category=IntegrationCategory.CHAT,
    display_name="Slack",
    client_id_setting="slack_oauth_client_id",
    client_secret_setting="slack_oauth_client_secret",
    authorize_url="https://slack.com/oauth/v2_user/authorize",
    token_url="https://slack.com/api/oauth.v2.user.access",
    revoke_url="https://slack.com/api/auth.revoke",
    default_scopes=(
        "channels:read",
        "channels:history",
        "groups:history",
        "mpim:history",
        "im:read",
        "im:write",
        "im:history",
        "chat:write",
        "users:read",
        "users:read.email",
        "reactions:write",
    ),
    scope_separator=",",
    use_pkce=True,
    token_endpoint_auth_method="client_secret_post",
    refresh_supported=True,
    api_base_url="https://slack.com/api",
    compliance_installer=install_slack_compliance,
    mcp_factory=slack_mcp_server,
)
