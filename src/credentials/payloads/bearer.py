"""Bearer-token payload (also covers PAT, custom-header API keys, query keys).

The token is the secret part and gets encrypted before storage. Placement
options (where the token goes in the request) live in ``BearerMetadata``,
which is stored as plaintext in the credential's metadata column. They are
not secret and keeping them readable simplifies debugging and rendering in
the integrations UI.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class BearerPayload(BaseModel):
    """Secret portion of a bearer credential."""

    token: str = Field(min_length=1)

    @field_validator("token")
    @classmethod
    def _strip_token(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("token cannot be blank.")
        return stripped


class BearerMetadata(BaseModel):
    """Non-secret options that describe how the token is sent on requests.

    Defaults match the most common case: ``Authorization: Bearer <token>``.
    For services like Postmark or Algolia that use a custom header, the
    caller sets ``placement="header"`` with a different ``header_name`` and
    typically clears ``prefix``. For legacy APIs that take the key in the
    query string, set ``placement="query"`` and supply ``param_name``.
    """

    placement: Literal["header", "query"] = "header"
    header_name: str = Field(default="Authorization", min_length=1, max_length=128)
    prefix: str = Field(default="Bearer ", max_length=32)
    param_name: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _validate_param_name(self) -> BearerMetadata:
        if self.placement == "query" and not self.param_name:
            raise ValueError("param_name is required when placement is 'query'.")
        return self


def bearer_preview(token: str) -> str:
    """Build a redacted preview safe to return through the API.

    Shows up to the first 5 characters (provider prefixes like ``sk_li``,
    ``ghp_``, ``lin_a``) and the last 4. Tokens shorter than 12 characters
    fall back to a simple mask so the preview never leaks the whole secret.
    """
    if len(token) < 12:
        return "***"
    return f"{token[:5]}***{token[-4:]}"
