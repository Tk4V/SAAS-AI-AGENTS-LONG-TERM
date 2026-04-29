"""OAuth credential payload.

The secret part is the access token plus an optional refresh token. The
non-secret half (provider, scopes, expiry, raw provider response) lives in
metadata so the resolver and refresher can inspect it without decryption.

``raw`` keeps the original provider response so per-provider quirks stashed
by post-callback hooks (Atlassian cloudId, Slack team_id, Google id_token)
remain accessible without bloating the schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OAuthPayload(BaseModel):
    """Secret portion of an OAuth credential."""

    access_token: str = Field(min_length=1)
    refresh_token: str | None = None


class OAuthMetadata(BaseModel):
    """Non-secret OAuth fields kept in plaintext for inspection and refresh."""

    provider: str = Field(min_length=1, max_length=64)
    scopes: tuple[str, ...] = ()
    expires_at: datetime | None = None
    needs_reauth: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


def oauth_preview(provider: str, scopes: tuple[str, ...]) -> str:
    """Render a non-sensitive preview for the integrations UI."""
    if not scopes:
        return f"oauth:{provider}"
    head = ", ".join(scopes[:3])
    suffix = "" if len(scopes) <= 3 else f" +{len(scopes) - 3}"
    return f"oauth:{provider} [{head}{suffix}]"
