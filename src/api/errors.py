"""Maps internal exceptions to HTTP responses.

The `ExceptionHandlerRegistry` is constructed once and attached to the
application during startup. Keeping the handlers as bound methods (rather than
free functions inside a `register(...)` call) makes them easy to override or
mock in tests.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.utils.exceptions import AppError


class ExceptionHandlerRegistry:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(__name__)

    def register(self, app: FastAPI) -> None:
        """Attach every handler this registry exposes onto the FastAPI app."""
        app.add_exception_handler(AppError, self.handle_app_error)
        app.add_exception_handler(RequestValidationError, self.handle_validation_error)
        app.add_exception_handler(StarletteHTTPException, self.handle_http_exception)
        app.add_exception_handler(Exception, self.handle_unexpected)

    @staticmethod
    def _payload(code: str, message: str, details: object | None = None) -> dict[str, object]:
        body: dict[str, object] = {"code": code, "message": message}
        if details:
            body["details"] = details
        return {"error": body}

    async def handle_app_error(self, request: Request, exc: AppError) -> JSONResponse:
        if exc.http_status >= 500:
            self._logger.exception(
                "Unhandled application error: %s",
                exc.message,
                extra={"path": request.url.path},
            )
        else:
            self._logger.info(
                "App error %s on %s: %s", exc.code, request.url.path, exc.message
            )
        return JSONResponse(
            status_code=exc.http_status,
            content=self._payload(exc.code, exc.message, exc.details),
        )

    async def handle_validation_error(
        self, request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=self._payload(
                "validation_error",
                "Request payload failed validation.",
                jsonable_encoder(exc.errors()),
            ),
        )

    async def handle_http_exception(
        self, request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=self._payload("http_error", str(exc.detail)),
        )

    async def handle_unexpected(self, request: Request, exc: Exception) -> JSONResponse:
        self._logger.exception("Unexpected error on %s", request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=self._payload("internal_error", "An unexpected error occurred."),
        )
