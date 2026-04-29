"""Credential-domain exceptions.

These extend ``AppError`` so they map to HTTP responses through the existing
exception handler registry without further wiring.
"""

from __future__ import annotations

from src.utils.exceptions import AppError


class InvalidCredentialPayload(AppError):
    """The submitted credential payload failed validation for its kind."""

    code = "invalid_credential_payload"
    http_status = 422


class CredentialKindNotSupported(AppError):
    """The requested credential kind is not registered."""

    code = "credential_kind_not_supported"
    http_status = 400


class CredentialAlreadyDeleted(AppError):
    """The credential has been soft-deleted and is no longer usable."""

    code = "credential_deleted"
    http_status = 410
