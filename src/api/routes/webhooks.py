"""GitHub webhook endpoint.

This route receives webhook events from GitHub. It does NOT use JWT
authentication — instead it verifies the payload's HMAC-SHA256 signature
using the shared GITHUB_WEBHOOK_SECRET. The raw body must be read before
FastAPI parses it so the HMAC covers the exact bytes GitHub signed.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from src.api.deps import SessionDep
from src.api.schemas.webhook_schemas import GitHubWorkflowRunPayload
from src.services.webhook_service import WebhookService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_logger = structlog.get_logger("clyde.route.webhooks")


def _get_webhook_service() -> WebhookService:
    """Factory — instantiated per request so tests can override settings."""
    return WebhookService()


@router.post("/github")
async def github_webhook(
    request: Request,
    session: SessionDep,
) -> JSONResponse:
    """Receive a GitHub webhook event.

    The endpoint reads the raw body first for HMAC verification, then parses
    the JSON based on the X-GitHub-Event header. Only ``workflow_run`` events
    are processed; everything else gets a 200 OK with no side effects.
    """
    service = _get_webhook_service()

    # 1. Read raw body for signature verification.
    raw_body = await request.body()

    # 2. Verify the signature.
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not signature:
        _logger.warning("webhook.missing_signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Hub-Signature-256 header.",
        )

    if not service.verify_signature(payload=raw_body, signature=signature):
        _logger.warning("webhook.invalid_signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        )

    # 3. Determine event type from GitHub's header.
    event_type = request.headers.get("X-GitHub-Event", "")
    _logger.info("webhook.received", event=event_type)

    # 4. Route based on event type.
    if event_type == "workflow_run":
        try:
            payload = GitHubWorkflowRunPayload.model_validate_json(raw_body)
        except Exception:
            _logger.exception("webhook.payload_parse_failed", event=event_type)
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"status": "error", "detail": "invalid payload"},
            )

        await service.handle_workflow_run(session=session, payload=payload)
    elif event_type == "ping":
        _logger.info("webhook.ping")
    else:
        _logger.debug("webhook.unhandled_event", event=event_type)

    return JSONResponse(content={"status": "ok"})
