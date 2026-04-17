"""Application-wide exception hierarchy.

Every error raised inside the service inherits from `AppError`. Errors are
mapped to HTTP responses by `src.api.errors`. Keep messages safe to expose to
the API client and put any sensitive context into structured logs instead.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all application-level errors."""

    code: str = "internal_error"
    http_status: int = 500

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message or self.__class__.__doc__ or self.__class__.__name__)
        self.message = message or (self.__class__.__doc__ or self.__class__.__name__).strip()
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class NotFoundError(AppError):
    """The requested resource does not exist or is not visible to the caller."""

    code = "not_found"
    http_status = 404


class AlreadyExistsError(AppError):
    """A resource with the same unique key already exists."""

    code = "already_exists"
    http_status = 409


class ValidationError(AppError):
    """The request payload failed validation rules."""

    code = "validation_error"
    http_status = 422


class AuthenticationError(AppError):
    """The caller did not provide valid credentials."""

    code = "authentication_error"
    http_status = 401


class AuthorizationError(AppError):
    """The caller is authenticated but not allowed to perform this action."""

    code = "authorization_error"
    http_status = 403


class ConflictError(AppError):
    """The request conflicts with the current state of the resource."""

    code = "conflict"
    http_status = 409


class ExternalServiceError(AppError):
    """An upstream dependency (LLM, GitHub, Docker, RDS) failed."""

    code = "external_service_error"
    http_status = 502


class PipelineError(AppError):
    """An error happened during pipeline execution and was not recoverable."""

    code = "pipeline_error"
    http_status = 500


class SandboxError(AppError):
    """The sandbox runner failed to start, timed out, or exceeded resource limits."""

    code = "sandbox_error"
    http_status = 500
