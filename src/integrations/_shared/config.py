"""OAuthProviderConfig — declarative description of one provider.

Each `<name>/config.py` declares a single instance of this dataclass. The
shape is intentionally flat: 20 fields beats 5 nested objects when a junior
dev needs to add their first provider.

Three blocks of fields:
1. Identity        — kind, category, display name.
2. OAuth endpoints — authorize/token/revoke URLs, or an OIDC discovery URL.
3. Behavior knobs  — scopes, PKCE, refresh, auth method, settings keys, hooks.

Anything provider-specific that does not fit these knobs (Atlassian cloudId
resolution, Salesforce `instance_url`) belongs in `<name>/compliance.py` and
is wired in via `compliance_installer`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

MCPFactory = Callable[[str, dict[str, Any]], dict[str, Any]]

PostCallbackHook = Callable[[str], Awaitable[dict[str, Any]]]
"""Post-OAuth callback hook: ``(access_token,) → extra_raw_metadata``.

Called by ``OAuthService.handle_callback`` immediately after the token
exchange. The returned dict is merged into ``raw_metadata`` on the stored
credential. Use this for any provider-specific discovery that must run once
at connect time (e.g. Atlassian cloud_id + site_url, Salesforce instance_url,
Slack team_id). Returning ``{}`` is valid — the hook is a no-op in that case.
"""
"""MCP server factory: ``(access_token, raw_metadata) → McpStdioServerConfig dict``.

Each provider that exposes an MCP server declares one function with this
signature and wires it into ``OAuthProviderConfig.mcp_factory``.
``BaseAgent.build_user_mcp_servers`` calls every registered factory for
credentials the user has connected — agents never need to know which
providers exist or what fields each one requires.
"""

from src.integrations._shared.kinds import IntegrationCategory, IntegrationKind

ComplianceInstaller = Callable[[Any], None]
"""Function that registers Authlib compliance hooks on an `AsyncOAuth2Client`."""

TokenRevoker = Callable[[str], Awaitable[None]]
"""Provider-specific token revocation. Takes the plaintext access token and
revokes it at the provider. Use this when the provider does not implement
RFC 7009 (e.g. GitHub OAuth Apps, which use DELETE with Basic auth instead).
"""


@dataclass(frozen=True, slots=True)
class OAuthProviderConfig:
    # Identity
    kind: IntegrationKind
    category: IntegrationCategory
    display_name: str

    # Settings keys (read lazily from `Settings` at client-build time).
    # Required so the framework can fetch client_id/secret without each
    # provider importing `Settings` directly.
    client_id_setting: str
    client_secret_setting: str

    # OAuth endpoints. Provide either (authorize_url + token_url) for a hand-
    # configured provider, or `server_metadata_url` for an OIDC provider that
    # publishes a discovery document.
    authorize_url: str | None = None
    token_url: str | None = None
    server_metadata_url: str | None = None
    revoke_url: str | None = None
    userinfo_url: str | None = None

    # Scopes
    default_scopes: tuple[str, ...] = ()
    scope_separator: str = " "  # GitHub uses ",", most others use " "

    # PKCE (RFC 7636). Almost always on; only disable for legacy providers.
    use_pkce: bool = True
    pkce_method: Literal["S256", "plain"] = "S256"

    # Token endpoint authentication (RFC 6749 §2.3, RFC 8414 §2).
    token_endpoint_auth_method: Literal[
        "client_secret_post", "client_secret_basic", "none"
    ] = "client_secret_post"

    # Refresh tokens. False for providers that issue long-lived tokens
    # without rotation (e.g. GitHub OAuth App).
    refresh_supported: bool = True

    # Extra params for the authorization request (e.g. Google's
    # `access_type=offline`, `prompt=consent`).
    extra_authorize_params: Mapping[str, str] = field(default_factory=dict)

    # API base URL — used by `<name>/client.py` to build endpoint paths.
    api_base_url: str | None = None

    # Compliance installer — direct function reference, no string keys.
    # Lives in `<name>/compliance.py`. Optional: most providers don't need it.
    compliance_installer: ComplianceInstaller | None = None

    # Custom revoker — for providers that do not implement RFC 7009.
    # When set, OAuthAdapter.revoke() calls this instead of the standard
    # revocation endpoint. The function takes the plaintext access token
    # and is responsible for the entire revocation request.
    custom_revoker: TokenRevoker | None = None

    # MCP server factory. When set, BaseAgent.build_user_mcp_servers() calls
    # this for every credential the user has connected, producing an entry in
    # ClaudeAgentOptions.mcp_servers. Leave None for providers that have no
    # MCP server (e.g. identity providers used only for auth).
    mcp_factory: MCPFactory | None = None

    # Post-callback hook. When set, OAuthService.handle_callback() calls this
    # with the fresh access token and merges the returned dict into raw_metadata
    # before persisting the credential. Use for provider-specific discovery
    # (Atlassian cloud_id, Salesforce instance_url, etc.).
    post_callback_hook: PostCallbackHook | None = None

    def __post_init__(self) -> None:
        has_endpoints = bool(self.authorize_url and self.token_url)
        has_discovery = bool(self.server_metadata_url)
        if not has_endpoints and not has_discovery:
            raise ValueError(
                f"Provider {self.kind.value!r} must declare either "
                "(authorize_url + token_url) or server_metadata_url."
            )
