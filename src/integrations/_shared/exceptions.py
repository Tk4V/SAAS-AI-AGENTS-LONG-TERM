"""Typed exceptions for the integration layer.

Keep provider-specific code raising these instead of bare Exception so callers
(routes, services, agents) can branch on the failure type without inspecting
strings. The hierarchy is intentionally shallow: one root error per concern.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base for any failure that originated in a provider integration."""


class ProviderAuthError(ProviderError):
    """OAuth dance failed: bad code, expired state, scope mismatch, refusal."""


class ProviderRefreshError(ProviderError):
    """Refresh token request failed or the provider revoked the grant."""


class ProviderApiError(ProviderError):
    """Provider API call returned a non-success status outside auth flows."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ProviderRateLimitError(ProviderApiError):
    """Provider returned 429 or equivalent. Caller may retry after `retry_after`."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: object) -> None:
        super().__init__(message, **kwargs)  # type: ignore[arg-type]
        self.retry_after = retry_after


class ProviderConfigError(ProviderError):
    """Provider is not registered, or its config is missing required fields."""
