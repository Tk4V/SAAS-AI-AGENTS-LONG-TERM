"""Authlib compliance hooks for the Atlassian OAuth 2.0 3LO flow.

Jira tokens expire (typically 3600 s) and rotate on every refresh:
each refresh call issues a new refresh token that replaces the old one.
`TokenResolver.resolve()` handles the expiry check and refresh cycle;
this module only patches the Authlib client for Atlassian-specific quirks.

The hooks registered here are intentional no-op normalizers for now —
Atlassian returns standard JSON that Authlib parses correctly. They exist
as a stable hook point for future Atlassian quirk fixes without touching
the core OAuth flow.
"""

from __future__ import annotations

from authlib.integrations.httpx_client import AsyncOAuth2Client


def _normalize_atlassian_token_response(response):
    """Ensure Authlib correctly parses Atlassian token responses.

    Atlassian returns standard OAuth 2.0 JSON (access_token, refresh_token,
    expires_in, scope, token_type). No transformation is currently needed;
    this hook is a stable extension point for future Atlassian quirks.
    """
    return response


def install_jira_compliance(client: AsyncOAuth2Client) -> None:
    """Register Atlassian compliance hooks on an AsyncOAuth2Client.

    Called once by AuthlibClientFactory immediately after construction;
    the patched client is then cached for the process lifetime.
    """
    client.register_compliance_hook(
        "access_token_response", _normalize_atlassian_token_response
    )
    client.register_compliance_hook(
        "refresh_token_response", _normalize_atlassian_token_response
    )
