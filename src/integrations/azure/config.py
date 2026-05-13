"""Azure OAuth provider declaration.

Azure uses OAuth 2.0 via Azure AD (Entra ID). The access token is scoped to
``https://management.azure.com/.default`` so the agent can call the Azure
Resource Manager REST API and any Azure MCP server that accepts Bearer tokens.

Token refresh is supported — Azure AD issues refresh tokens with
``offline_access`` scope that can be exchanged for new access tokens.
"""

from __future__ import annotations

from src.agent_tools.mcp.azure import azure_mcp_server
from src.integrations._shared.config import OAuthProviderConfig
from src.integrations._shared.kinds import IntegrationCategory, IntegrationKind

AZURE = OAuthProviderConfig(
    kind=IntegrationKind.AZURE,
    category=IntegrationCategory.CLOUD,
    display_name="Azure",
    client_id_setting="azure_oauth_client_id",
    client_secret_setting="azure_oauth_client_secret",
    # Azure AD v2 endpoints — tenant "common" accepts both personal and
    # work/school accounts. Replace with a specific tenant ID if needed.
    authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    revoke_url=None,  # Azure AD does not implement RFC 7009 revocation
    default_scopes=(
        "https://management.azure.com/.default",
        "offline_access",
    ),
    scope_separator=" ",
    use_pkce=True,
    refresh_supported=True,
    token_endpoint_auth_method="client_secret_post",
    api_base_url="https://management.azure.com",
    mcp_factory=azure_mcp_server,
)
