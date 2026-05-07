"""Application-wide exception hierarchy.

Every error raised inside the service inherits from ``AppError``. Errors are
mapped to HTTP responses by ``src.api.errors``. Keep messages safe to expose
to the API client and put sensitive context into structured logs instead.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all application-level errors.

    Subclasses define ``code`` (machine-readable) and ``http_status``
    so the error handler can render the correct HTTP response without
    inspecting the exception type at runtime.
    """

    code: str = "internal_error"
    http_status: int = 500

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Create an application error.

        Args:
            message: Human-readable error message for the API client.
            details: Structured context for logging and debugging.
        """
        resolved_message = message or (self.__class__.__doc__ or self.__class__.__name__).strip()
        super().__init__(resolved_message)
        self.message = resolved_message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Serialize the error for JSON API responses."""
        return {"code": self.code, "message": self.message, "details": self.details}


class NotFoundError(AppError):
    """The requested resource does not exist or is not visible to the caller."""

    code = "not_found"
    http_status = 404


class AlreadyExistsError(AppError):
    """A resource with the same unique key already exists."""

    code = "already_exists"
    http_status = 409


class AuthenticationError(AppError):
    """The caller did not provide valid credentials."""

    code = "authentication_error"
    http_status = 401


class AuthorizationError(AppError):
    """The caller is authenticated but lacks permission for this action."""

    code = "authorization_error"
    http_status = 403


class ConflictError(AppError):
    """The request conflicts with the current state of the resource."""

    code = "conflict"
    http_status = 409


class ExternalServiceError(AppError):
    """An upstream dependency (LLM, GitHub, RDS) failed."""

    code = "external_service_error"
    http_status = 502


class WebhookRetryLater(AppError):
    """Webhook arrived while the underlying task is not in a state we can act
    on (typically the initial pipeline is still running). Returning 503 asks
    the sender (GitHub) to retry the delivery after a short backoff."""

    code = "webhook_retry_later"
    http_status = 503


class PipelineError(AppError):
    """An error happened during pipeline execution and was not recoverable."""

    code = "pipeline_error"
    http_status = 500


class ValidationError(AppError):
    """The request payload failed a business-rule check the framework can't catch."""

    code = "validation_error"
    http_status = 422
