"""Process-wide registry of active chat sessions.

One ``SDKChatSession`` exists per running task chat. The registry keeps
a reference so:

* The WebSocket handler can call ``request_close(task_id)`` when the
  user sends ``close_session``;
* The app lifespan can shut every active session down gracefully on
  uvicorn restart (Phase 3);
* Diagnostics endpoints can list / inspect what's currently running.

This is in-process state and does NOT survive an app restart. Phase 3
adds a startup hook that marks orphaned tasks as failed so they don't
stay zombie in the DB forever.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog

from src.agents.chat.session import SDKChatSession, SessionEndReason


class ChatSessionService:
    def __init__(self) -> None:
        self._sessions: dict[UUID, SDKChatSession] = {}
        self._tasks: dict[UUID, asyncio.Task[SessionEndReason]] = {}
        self._lock = asyncio.Lock()
        self._logger = structlog.get_logger("clyde.service.chat_session")

    async def register_and_run(self, session: SDKChatSession) -> asyncio.Task[SessionEndReason]:
        """Add the session to the registry and spawn its run() coroutine.

        The returned task can be awaited by callers that want to block
        until the session ends, but typically the session lives on its own
        and is observed via WebSocket events / DB status changes.
        """
        async with self._lock:
            if session.task_id in self._tasks:
                raise RuntimeError(
                    f"A chat session for task {session.task_id} is already running"
                )
            self._sessions[session.task_id] = session
            task = asyncio.create_task(
                self._run_and_unregister(session),
                name=f"chat-session-{session.task_id}",
            )
            self._tasks[session.task_id] = task
        self._logger.info("chat_session.registered", task_id=str(session.task_id))
        return task

    async def _run_and_unregister(self, session: SDKChatSession) -> SessionEndReason:
        try:
            return await session.run()
        finally:
            async with self._lock:
                self._sessions.pop(session.task_id, None)
                self._tasks.pop(session.task_id, None)
            self._logger.info(
                "chat_session.unregistered", task_id=str(session.task_id)
            )

    def get(self, task_id: UUID) -> SDKChatSession | None:
        return self._sessions.get(task_id)

    def is_active(self, task_id: UUID) -> bool:
        return task_id in self._sessions

    def active_task_ids(self) -> list[UUID]:
        return list(self._sessions.keys())

    def request_close(
        self, task_id: UUID, reason: SessionEndReason = SessionEndReason.USER_CLOSED
    ) -> bool:
        """Ask the session for ``task_id`` to wind down. Returns False if
        no such session is currently active."""
        session = self._sessions.get(task_id)
        if session is None:
            return False
        session.request_close(reason)
        return True

    async def shutdown_all(self, *, grace_sec: float = 5.0) -> None:
        """Used by the app lifespan on uvicorn shutdown. Asks each session
        to close, waits up to ``grace_sec`` total for them, then cancels
        any stragglers. Marking-zombies-as-failed lives in the lifespan
        startup hook, not here — at shutdown we just stop cleanly."""
        if not self._sessions:
            return
        self._logger.info(
            "chat_session.shutdown_all_starting", count=len(self._sessions)
        )
        for session in list(self._sessions.values()):
            session.request_close(SessionEndReason.USER_CLOSED)
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks.values(), return_exceptions=True),
                timeout=grace_sec,
            )
        except asyncio.TimeoutError:
            self._logger.warning(
                "chat_session.shutdown_all_grace_exceeded",
                still_running=len(self._tasks),
            )
            for task in self._tasks.values():
                task.cancel()


chat_session_service = ChatSessionService()
