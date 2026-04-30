"""Signs and verifies the OAuth `state` parameter as a short-lived JWT.

State is the only thing that survives the round-trip between our backend,
the provider, and the user's browser. Embedding `user_id`, `provider`, and
the optional PKCE verifier in a JWT means the callback route is stateless:
no Starlette session, no Redis, no `state→user` table.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from src.config import Settings, get_settings
from src.utils.exceptions import AuthenticationError


class OAuthStateSigner:
    """Issues and validates state tokens.

    JWT claims:
    - `type`         — fixed string, prevents misusing app JWTs as state tokens
    - `user_id`      — who initiated the flow (we attribute the granted token to them)
    - `provider`     — which kind they connected; the callback verifies it matches the URL
    - `pkce_verifier`— optional, only present when `use_pkce=True` for the provider
    - `nonce`        — random per-call, breaks deterministic-state replay
    - `iat`/`exp`    — short TTL (default 10 min) bounded by `Settings.oauth_state_ttl_sec`
    """

    TOKEN_TYPE = "oauth_state"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def sign(
        self,
        *,
        user_id: int,
        provider: str,
        pkce_verifier: str | None = None,
    ) -> str:
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "type": self.TOKEN_TYPE,
            "user_id": user_id,
            "provider": provider,
            "nonce": secrets.token_urlsafe(16),
            "iat": int(now.timestamp()),
            "exp": int(
                (now + timedelta(seconds=self._settings.oauth_state_ttl_sec)).timestamp()
            ),
        }
        if pkce_verifier is not None:
            payload["pkce_verifier"] = pkce_verifier
        return jwt.encode(
            payload,
            self._settings.jwt_secret.get_secret_value(),
            algorithm=self._settings.jwt_algorithm,
        )

    def verify(self, state: str) -> dict[str, Any]:
        try:
            claims = jwt.decode(
                state,
                self._settings.jwt_secret.get_secret_value(),
                algorithms=[self._settings.jwt_algorithm],
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("OAuth state has expired.") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("OAuth state is invalid.") from exc

        if claims.get("type") != self.TOKEN_TYPE:
            raise AuthenticationError("Token presented as OAuth state is not one.")
        if not isinstance(claims.get("user_id"), int):
            raise AuthenticationError("OAuth state is missing user_id.")
        return claims
