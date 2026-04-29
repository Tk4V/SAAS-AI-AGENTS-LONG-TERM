"""Common interface every credential kind must implement.

A handler converts between the public schemas (what the API accepts and
returns) and the storage schemas (what we serialise into the encrypted blob
and the metadata column). Centralising this contract lets ``CredentialService``
treat all kinds uniformly.
"""

from __future__ import annotations

from typing import Any, Protocol

from src.db.models.credential import CredentialKind


class KindHandler(Protocol):
    """Per-kind serialisation and preview logic."""

    kind: CredentialKind

    def parse_payload(self, raw: dict[str, Any]) -> Any:
        """Validate and parse the secret payload from a request body."""

    def parse_metadata(self, raw: dict[str, Any] | None) -> Any:
        """Validate and parse the non-secret metadata from a request body."""

    def serialise_payload(self, payload: Any) -> str:
        """Render a payload object as a JSON string for encryption."""

    def deserialise_payload(self, raw: str) -> Any:
        """Re-hydrate a payload object from a decrypted JSON string."""

    def serialise_metadata(self, metadata: Any) -> dict[str, Any]:
        """Render metadata as a JSONB-friendly dict for storage."""

    def deserialise_metadata(self, raw: dict[str, Any]) -> Any:
        """Re-hydrate a metadata object from a stored dict."""

    def build_preview(self, payload: Any) -> str:
        """Return a redacted preview safe to expose through the API."""
