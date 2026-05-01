"""Slack OAuth provider declaration.

Slack uses a non-standard OAuth response: ``200 OK`` with ``ok: false`` for
errors, hence the compliance hook. Token rotation is opt-in per app config —
when enabled Slack issues 12h-life tokens that rotate on refresh; we declare
``refresh_supported=True`` and the resolver will only attempt refresh when
an ``expires_at`` is present.

Default scopes target a bot user that can read channels and post messages,
which covers most agent automations. Apps that need richer scopes should
request them explicitly at authorize time.
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
    authorize_url="https://slack.com/oauth/v2/authorize",
    token_url="https://slack.com/api/oauth.v2.access",
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
    use_pkce=False,
    token_endpoint_auth_method="client_secret_post",
    refresh_supported=True,
    api_base_url="https://slack.com/api",
    compliance_installer=install_slack_compliance,
    mcp_factory=slack_mcp_server,
)
