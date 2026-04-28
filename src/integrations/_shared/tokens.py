"""TokenBundle: the standardized result of any OAuth exchange or refresh.

Authlib hands back a dict-like `OAuth2Token`; we normalize it into this
frozen dataclass so the rest of the codebase (DB layer, services, agents)
sees one shape regardless of which provider returned the token.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class TokenBundle:
    """Normalized OAuth token data. Immutable so callers cannot accidentally
    leak mutated copies.

    `raw` keeps the original provider response so per-provider compliance
    code can stash quirks (Slack `team_id`, Atlassian `cloudId`, Salesforce
    `instance_url`) without bloating this class.
    """

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_authlib(
        cls,
        token: Mapping[str, Any],
        *,
        default_scopes: tuple[str, ...] = (),
        scope_separator: str = " ",
    ) -> TokenBundle:
        """Build a TokenBundle from Authlib's `fetch_token` / `refresh_token` output."""
        access = token.get("access_token")
        if not isinstance(access, str) or not access:
            raise ValueError("Token response is missing `access_token`.")

        expires_at: datetime | None = None
        if "expires_at" in token:
            expires_at = datetime.fromtimestamp(int(token["expires_at"]), tz=UTC)
        elif "expires_in" in token:
            expires_at = datetime.now(UTC) + timedelta(seconds=int(token["expires_in"]))

        scope_value = token.get("scope") or ""
        if isinstance(scope_value, str) and scope_value:
            parts = tuple(s for s in scope_value.split(scope_separator) if s)
        else:
            parts = default_scopes

        refresh = token.get("refresh_token")
        return cls(
            access_token=access,
            refresh_token=refresh if isinstance(refresh, str) else None,
            expires_at=expires_at,
            scopes=parts,
            raw=dict(token),
        )

    def is_expired(self, *, skew_seconds: int = 60) -> bool:
        """True if the token is past its expiry (with a small clock-skew margin).

        Tokens without `expires_at` are treated as non-expiring.
        """
        if self.expires_at is None:
            return False
        threshold = datetime.now(UTC) + timedelta(seconds=skew_seconds)
        return self.expires_at <= threshold
