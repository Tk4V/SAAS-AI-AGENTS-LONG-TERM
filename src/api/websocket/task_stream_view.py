"""Bidirectional WebSocket chat for a task.

Endpoint:

    ws://host/api/v1/ws/tasks/{task_id}/chat?token=<jwt>

Two cooperating coroutines run for the lifetime of the connection:

* ``_send_loop`` — drains the broadcaster queue and forwards every event
  to the client as JSON. Outbound events come from agents (status,
  approval requests, chat messages) and from the pipeline runner
  (status changes, errors).
* ``_recv_loop`` — reads JSON envelopes from the client and routes them:
  ``approval_response`` resolves a pending approval through the Redis
  permission gate, ``chat_message`` appends a free-form user message
  and broadcasts it back, ``ping`` is a no-op keepalive.

Both inbound and outbound messages are persisted to ``task_messages`` so
the UI can rebuild the conversation after a reload via
``GET /tasks/{id}/messages``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import TypeAdapter, ValidationError

from src.api.dependencies import get_current_user_ws
from src.api.schemas.task_message_schemas import (
    WSApprovalResponse,
    WSChatMessage,
    WSInboundMessage,
    WSPing,
)
from src.config.constants import (
    WS_EVENT_APPROVAL_RESOLVED,
    WS_EVENT_USER_MESSAGE,
)
from src.db.models.task_approval import ApprovalStatus
from src.db.models.task_message import MessageKind, MessageRole
from src.db.queries.task_approval_query import TaskApprovalRepository
from src.db.queries.task_message_query import TaskMessageRepository
from src.db.queries.task_query import TaskRepository
from src.db.session import db
from src.utils.broadcaster import broadcaster
from src.utils.exceptions import AuthenticationError, ConflictError, NotFoundError

router = APIRouter(tags=["ws"])

_inbound_adapter: TypeAdapter[WSInboundMessage] = TypeAdapter(WSInboundMessage)


class TaskChatView:
    _logger = structlog.get_logger("clyde.ws.task_chat")

    @staticmethod
    @router.websocket("/ws/tasks/{task_id}/chat")
    async def chat(websocket: WebSocket, task_id: UUID) -> None:
        logger = TaskChatView._logger

        try:
            current_user = await get_current_user_ws(websocket)
        except AuthenticationError:
            return

        await websocket.accept()
        log = logger.bind(task_id=str(task_id), user_id=current_user.id)

        try:
            async with db.session_scope() as session:
                await TaskRepository(session).get(
                    user_id=current_user.id, task_id=task_id
                )
        except NotFoundError:
            log.warning("ws.task_not_found")
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION, reason="Task not found"
            )
            return

        queue, unsubscribe = await broadcaster.subscribe(task_id)
        log.info("ws.connected")

        send_task = asyncio.create_task(
            _send_loop(websocket, queue, log),
            name=f"ws-send-{task_id}",
        )
        recv_task = asyncio.create_task(
            _recv_loop(websocket, task_id, current_user.id, log),
            name=f"ws-recv-{task_id}",
        )

        try:
            done, pending = await asyncio.wait(
                {send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            for t in pending:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            for t in done:
                exc = t.exception()
                if exc is not None and not isinstance(exc, WebSocketDisconnect):
                    log.exception("ws.loop_failed", error=str(exc))
        finally:
            await unsubscribe()
            try:
                await websocket.close()
            except Exception:
                pass
            log.info("ws.closed")


async def _send_loop(
    websocket: WebSocket,
    queue: asyncio.Queue[dict | None],
    log: structlog.stdlib.BoundLogger,
) -> None:
    while True:
        event = await queue.get()
        if event is None:
            return
        try:
            await websocket.send_text(json.dumps(event))
        except WebSocketDisconnect:
            return
        except Exception:
            log.exception("ws.send_failed")
            return


async def _recv_loop(
    websocket: WebSocket,
    task_id: UUID,
    user_id: int,
    log: structlog.stdlib.BoundLogger,
) -> None:
    while True:
        try:
            raw = await websocket.receive_text()
        except WebSocketDisconnect:
            return

        try:
            envelope = _inbound_adapter.validate_python(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            await _send_error(websocket, "invalid_envelope", str(exc))
            continue

        if isinstance(envelope, WSPing):
            continue
        if isinstance(envelope, WSChatMessage):
            await _handle_chat_message(task_id, user_id, envelope, log)
            continue
        if isinstance(envelope, WSApprovalResponse):
            try:
                await _handle_approval_response(task_id, user_id, envelope, log)
            except NotFoundError as exc:
                await _send_error(websocket, "not_found", str(exc))
            except ConflictError as exc:
                await _send_error(websocket, "conflict", str(exc))
            continue


async def _handle_chat_message(
    task_id: UUID,
    user_id: int,
    envelope: WSChatMessage,
    log: structlog.stdlib.BoundLogger,
) -> None:
    async with db.session_scope() as session:
        message = await TaskMessageRepository(session).create(
            user_id=user_id,
            task_id=task_id,
            role=MessageRole.USER,
            kind=MessageKind.CHAT,
            content=envelope.content,
        )
        message_id = message.id

    await broadcaster.publish(task_id, {
        "name": WS_EVENT_USER_MESSAGE,
        "agent": None,
        "payload": {
            "message_id": str(message_id),
            "content": envelope.content,
        },
        "occurred_at": datetime.now(UTC).isoformat(),
    })
    log.info("ws.chat_message", message_id=str(message_id))


async def _handle_approval_response(
    task_id: UUID,
    user_id: int,
    envelope: WSApprovalResponse,
    log: structlog.stdlib.BoundLogger,
) -> None:
    # Lazy import — keeps the WS module importable in test environments
    # where Redis is not available at import time.
    from src.agent_tools import permission_gate

    async with db.session_scope() as session:
        approval_repo = TaskApprovalRepository(session)
        approval = await approval_repo.get(
            user_id=user_id, task_id=task_id, approval_id=envelope.approval_id
        )
        if approval.status != ApprovalStatus.PENDING:
            raise ConflictError(
                f"Approval {envelope.approval_id} is already {approval.status.value}."
            )
        new_status = (
            ApprovalStatus.APPROVED if envelope.approved else ApprovalStatus.DENIED
        )
        approval = await approval_repo.resolve(
            approval_id=envelope.approval_id,
            status=new_status,
            user_response=envelope.payload or None,
        )

        await TaskMessageRepository(session).create(
            user_id=user_id,
            task_id=task_id,
            role=MessageRole.USER,
            kind=MessageKind.APPROVAL_RESPONSE,
            content="" if not envelope.payload else json.dumps(envelope.payload),
            meta={
                "approval_id": str(envelope.approval_id),
                "approved": envelope.approved,
                "tool_name": approval.tool_name,
            },
        )

    await permission_gate.resolve(
        approval_id=envelope.approval_id,
        approved=envelope.approved,
        payload=envelope.payload,
    )

    await broadcaster.publish(task_id, {
        "name": WS_EVENT_APPROVAL_RESOLVED,
        "agent": None,
        "payload": {
            "approval_id": str(envelope.approval_id),
            "approved": envelope.approved,
            "status": new_status.value,
        },
        "occurred_at": datetime.now(UTC).isoformat(),
    })
    log.info(
        "ws.approval_resolved",
        approval_id=str(envelope.approval_id),
        approved=envelope.approved,
    )


async def _send_error(websocket: WebSocket, code: str, detail: str) -> None:
    try:
        await websocket.send_text(
            json.dumps({"type": "error", "code": code, "detail": detail[:500]})
        )
    except Exception:
        pass
