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

from src.api.dependencies import get_current_user_ws
from src.utils.broadcaster import broadcaster
from src.db.queries.task_query import TaskRepository
from src.db.session import db
from src.utils.exceptions import AuthenticationError, NotFoundError

router = APIRouter(tags=["ws"])


class TaskStreamView:
    """WebSocket view that streams pipeline events for a single task."""

    _logger = structlog.get_logger("clyde.ws.task_stream")

    @staticmethod
    @router.websocket("/ws/tasks/{task_id}")
    async def stream(websocket: WebSocket, task_id: UUID) -> None:
        """Stream pipeline events for a task over WebSocket.

        Authenticates the user via JWT query parameter, verifies task
        ownership, then reads from the broadcaster queue until the
        pipeline finishes or the client disconnects.
        """
        logger = TaskStreamView._logger

        try:
            current_user = await get_current_user_ws(websocket)
        except AuthenticationError:
            return

        await websocket.accept()

        log = logger.bind(task_id=str(task_id), user_id=current_user.id)

        try:
            async with db.session_scope() as session:
                repository = TaskRepository(session)
                await repository.get(user_id=current_user.id, task_id=task_id)
        except NotFoundError:
            log.warning("ws.task_not_found")
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION, reason="Task not found"
            )
            return

        queue, unsubscribe = broadcaster.subscribe(task_id)
        log.info("ws.connected")

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                await websocket.send_text(json.dumps(event))
        except WebSocketDisconnect:
            log.info("ws.client_disconnected")
        except Exception:
            log.exception("ws.unexpected_error")
        finally:
            unsubscribe()
            try:
                await websocket.close()
            except Exception:
                pass
            log.info("ws.closed")
