"""Concrete kind handlers and the registry that exposes them.

Adding a new kind means writing a handler class and appending it to
``_DEFAULT_HANDLERS``. The service layer never imports a handler directly;
it asks the registry for the handler matching the credential's kind.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import ValidationError

from src.credentials.exceptions import (
    CredentialKindNotSupported,
    InvalidCredentialPayload,
)
from src.credentials.payloads.bearer import (
    BearerMetadata,
    BearerPayload,
    bearer_preview,
)
from src.credentials.payloads.oauth import (
    OAuthMetadata,
    OAuthPayload,
)
from src.db.models.credential import CredentialKind


class BearerKindHandler:
    """Handler for ``CredentialKind.BEARER``."""

    kind = CredentialKind.BEARER

    def parse_payload(self, raw: dict[str, Any]) -> BearerPayload:
        try:
            return BearerPayload(**raw)
        except ValidationError as exc:
            raise InvalidCredentialPayload(
                "Bearer payload is invalid.",
                details={"errors": exc.errors()},
            ) from exc

    def parse_metadata(self, raw: dict[str, Any] | None) -> BearerMetadata:
        try:
            return BearerMetadata(**(raw or {}))
        except ValidationError as exc:
            raise InvalidCredentialPayload(
                "Bearer metadata is invalid.",
                details={"errors": exc.errors()},
            ) from exc

    def serialise_payload(self, payload: BearerPayload) -> str:
        return payload.model_dump_json()

    def deserialise_payload(self, raw: str) -> BearerPayload:
        return BearerPayload(**json.loads(raw))

    def serialise_metadata(self, metadata: BearerMetadata) -> dict[str, Any]:
        return metadata.model_dump()

    def deserialise_metadata(self, raw: dict[str, Any]) -> BearerMetadata:
        return BearerMetadata(**raw)

    def build_preview(self, payload: BearerPayload) -> str:
        return bearer_preview(payload.token)


class OAuthKindHandler:
    """Handler for ``CredentialKind.OAUTH``.

    The OAuth flow (start, callback, refresh) lives in
    ``credentials.oauth.service``; this handler only describes how the
    resulting tokens are serialised, deserialised and previewed.
    """

    kind = CredentialKind.OAUTH

    def parse_payload(self, raw: dict[str, Any]) -> OAuthPayload:
        try:
            return OAuthPayload(**raw)
        except ValidationError as exc:
            raise InvalidCredentialPayload(
                "OAuth payload is invalid.",
                details={"errors": exc.errors()},
            ) from exc

    def parse_metadata(self, raw: dict[str, Any] | None) -> OAuthMetadata:
        try:
            return OAuthMetadata(**(raw or {}))
        except ValidationError as exc:
            raise InvalidCredentialPayload(
                "OAuth metadata is invalid.",
                details={"errors": exc.errors()},
            ) from exc

    def serialise_payload(self, payload: OAuthPayload) -> str:
        return payload.model_dump_json()

    def deserialise_payload(self, raw: str) -> OAuthPayload:
        return OAuthPayload(**json.loads(raw))

    def serialise_metadata(self, metadata: OAuthMetadata) -> dict[str, Any]:
        return metadata.model_dump(mode="json")

    def deserialise_metadata(self, raw: dict[str, Any]) -> OAuthMetadata:
        return OAuthMetadata(**raw)

    def build_preview(self, payload: OAuthPayload) -> str:
        # Preview is rebuilt at create-time using metadata; this fallback
        # keeps the protocol total when only payload is available.
        return "oauth:***"


_DEFAULT_HANDLERS: tuple[Any, ...] = (
    BearerKindHandler(),
    OAuthKindHandler(),
)


class KindRegistry:
    """Maps a ``CredentialKind`` to its handler."""

    def __init__(self, handlers: tuple[Any, ...] = _DEFAULT_HANDLERS) -> None:
        self._handlers: dict[CredentialKind, Any] = {h.kind: h for h in handlers}

    def get(self, kind: CredentialKind) -> Any:
        handler = self._handlers.get(kind)
        if handler is None:
            raise CredentialKindNotSupported(
                f"Credential kind {kind.value!r} is not supported.",
            )
        return handler


@lru_cache(maxsize=1)
def get_kind_registry() -> KindRegistry:
    return KindRegistry()
