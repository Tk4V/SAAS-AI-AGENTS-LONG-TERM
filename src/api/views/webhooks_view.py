"""GitHub webhook endpoint.

Receives webhook events from GitHub and delegates all processing
to WebhookService. The view only extracts HTTP-layer data (headers,
body) and passes it to the service.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from src.api.dependencies import SessionDep
from src.services.webhook_service import WebhookService
from src.utils.exceptions import AuthenticationError, WebhookRetryLater

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

_logger = structlog.get_logger("clyde.webhook")


class WebhookView:
    """Receive and process GitHub webhook events."""

    @staticmethod
    @router.post("/github")
    async def github(request: Request, session: SessionDep) -> JSONResponse:
        """Receive a GitHub webhook event and delegate to the service."""
        service = WebhookService()

        raw_body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")
        event_type = request.headers.get("X-GitHub-Event", "")

        try:
            result = await service.process_github_event(
                raw_body=raw_body,
                signature=signature,
                event_type=event_type,
                session=session,
            )
            return JSONResponse(content=result)
        except AuthenticationError as exc:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"status": "error", "detail": str(exc)},
            )
        except WebhookRetryLater as exc:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "retry_later", "detail": str(exc)},
                headers={"Retry-After": "30"},
            )
        except Exception as exc:
            _logger.exception(
                "webhook.invalid_payload",
                event_type=event_type,
                error=str(exc),
                body_preview=raw_body[:500].decode(errors="replace"),
            )
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"status": "error", "detail": "invalid payload"},
            )
