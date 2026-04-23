"""JWT validation against the shared Django DRF secret.

The Django service issues access tokens; we never call back to Django to verify
them. Both services agree on the HS256 secret stored in the JWT_SECRET env var
and the same audience claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import jwt
from jwt import (
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidSignatureError,
    InvalidTokenError,
)

from src.common.exceptions import AuthenticationError
from src.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Authenticated principal extracted from a JWT.

    `id` is the Django user primary key. The optional fields are populated when
    Django includes them in the token; downstream code must never assume they
    are present.
    """

    id: int
    username: str | None = None
    email: str | None = None
    raw_claims: dict[str, Any] = field(default_factory=dict)


class AuthService:
    """Validates JWTs minted by the Django DRF service.

    Settings are injected so that the same class can be used in tests with a
    different secret or audience without reaching for global state.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def decode_token(self, token: str) -> dict[str, Any]:
        """Decode and validate a token, returning its raw claim set.

        Audience verification is opt-in: it runs only when JWT_AUDIENCE is
        configured, since some DRF deployments do not include the `aud` claim.
        """
        verify_aud = bool(self._settings.jwt_audience)
        try:
            return jwt.decode(
                token,
                key=self._settings.jwt_secret.get_secret_value(),
                algorithms=[self._settings.jwt_algorithm],
                audience=self._settings.jwt_audience or None,
                options={"verify_aud": verify_aud},
                leeway=10,
            )
        except ExpiredSignatureError as exc:
            raise AuthenticationError("Token has expired.") from exc
        except InvalidSignatureError as exc:
            raise AuthenticationError("Token signature is invalid.") from exc
        except InvalidAudienceError as exc:
            raise AuthenticationError(
                "Token audience does not match this service.",
            ) from exc
        except InvalidTokenError as exc:
            raise AuthenticationError(
                "Token is malformed or otherwise invalid.",
            ) from exc

    def current_user_from_token(self, token: str) -> CurrentUser:
        """Decode a token and turn its claims into a `CurrentUser`.

        Expected claim layout matches djangorestframework-simplejwt defaults:
        `user_id` is the integer Django PK, `username` and `email` are optional.
        """
        claims = self.decode_token(token)

        user_id = claims.get("user_id")
        if user_id is None:
            raise AuthenticationError("Token is missing the user_id claim.")
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            raise AuthenticationError("Token user_id claim is not a valid integer.")

        return CurrentUser(
            id=user_id,
            username=claims.get("username"),
            email=claims.get("email"),
            raw_claims=claims,
        )
