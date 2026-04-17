"""WebSocket endpoint for streaming pipeline events to the frontend.

Clients connect with their JWT in the query string:

    ws://host/api/v1/ws/tasks/{task_id}?token=<jwt>

The handler validates the token, confirms the user owns the task, then
subscribes to the in-memory broadcaster and forwards every event as JSON
until the pipeline finishes (sentinel None) or the client disconnects.
"""

from __future__ import annotations

import json
from uuid import UUID

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from src.api.deps import get_current_user_ws
from src.common.exceptions import AuthenticationError, NotFoundError
from src.db.queries.task_queries import TaskRepository
from src.db.session import db
from src.engine.broadcaster import broadcaster

router = APIRouter(tags=["ws"])
_logger = structlog.get_logger("clyde.ws.task_stream")


@router.websocket("/ws/tasks/{task_id}")
async def task_event_stream(websocket: WebSocket, task_id: UUID) -> None:
    """Stream pipeline events for a single task over WebSocket."""

    # Authenticate before accepting the connection so we can reject with
    # a meaningful close code if the token is missing or invalid.
    try:
        current_user = await get_current_user_ws(websocket)
    except AuthenticationError:
        # get_current_user_ws already closed the socket with 1008.
        return

    await websocket.accept()

    log = _logger.bind(task_id=str(task_id), user_id=current_user.id)

    # Verify the caller owns the task. We use a standalone session because
    # there is no request-scoped session inside a WebSocket handler.
    try:
        async with db.session_scope() as session:
            repo = TaskRepository(session)
            await repo.get(user_id=current_user.id, task_id=task_id)
    except NotFoundError:
        log.warning("ws.task_not_found")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Task not found")
        return

    queue, unsubscribe = broadcaster.subscribe(task_id)
    log.info("ws.connected")

    try:
        while True:
            event = await queue.get()
            if event is None:
                # Pipeline finished; send nothing more.
                break
            await websocket.send_text(json.dumps(event))
    except WebSocketDisconnect:
        log.info("ws.client_disconnected")
    except Exception:
        log.exception("ws.unexpected_error")
    finally:
        unsubscribe()
        # Close gracefully if the socket is still open.
        try:
            await websocket.close()
        except Exception:
            pass
        log.info("ws.closed")
