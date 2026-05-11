"""Long-lived chat session wrapping a single ``ClaudeSDKClient``.

This is the core of CA-113's persistent-chat model. One ``SDKChatSession``
runs per active task in a dedicated background coroutine. It:

* opens a single ``ClaudeSDKClient`` with all the agent's tools, hooks,
  MCP servers, and system prompt;
* feeds the initial task description as the first user turn;
* streams assistant messages → broadcaster events + ``task_messages`` rows;
* between turns, blocks on the Redis ``TaskInputQueue`` for the next user
  message (with idle/hard timeouts);
* invokes a ``post_turn_callback`` after each turn so the publisher can
  commit + push any new diffs automatically;
* tears the client down cleanly on close.

Termination reasons are reported via ``SessionEndReason`` so the
orchestrating code can persist an accurate task status.

The session is NOT resumable across app restarts — the SDK keeps the
conversation in-process. Restart safeguards live in the app lifespan
(see Phase 3) which marks orphaned tasks as failed at startup.
"""

from __future__ import annotations

import enum
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    UserMessage,
)

from src.agents.chat.lifecycle import Lifecycle
from src.config.constants import (
    WS_EVENT_AGENT_MESSAGE,
    WS_EVENT_AGENT_THINKING,
    WS_EVENT_AGENT_TURN_FINISHED,
    WS_EVENT_SESSION_CLOSED,
    WS_EVENT_SESSION_TIMED_OUT,
    WS_EVENT_TASK_STATUS_CHANGED,
)
from src.db.models.task import TaskStatus
from src.db.models.task_message import MessageKind, MessageRole
from src.db.queries.task_message_query import TaskMessageRepository
from src.db.queries.task_query import TaskRepository
from src.db.session import db
from src.utils.broadcaster import broadcaster
from src.utils.task_input_queue import task_input_queue

if TYPE_CHECKING:
    pass


class SessionEndReason(str, enum.Enum):
    USER_CLOSED = "user_closed"
    IDLE_TIMEOUT = "idle_timeout"
    HARD_TIMEOUT = "hard_timeout"
    ERROR = "error"


# Callback fired after every completed turn that produced something. The
# orchestrating layer wires this to the publisher so each turn's diff is
# pushed to git automatically. The callback receives the session so it can
# read self.summary_of_last_turn / self.workspace_path / etc.
PostTurnCallback = Callable[["SDKChatSession", "TurnResult"], Awaitable[None]]


class TurnResult:
    """What the SDK produced in a single user→agent turn."""

    def __init__(
        self,
        *,
        turn_index: int,
        assistant_text: str,
        cost_usd: float,
        result_length: int,
    ) -> None:
        self.turn_index = turn_index
        self.assistant_text = assistant_text
        self.cost_usd = cost_usd
        self.result_length = result_length


class SDKChatSession:
    """Owns one ``ClaudeSDKClient`` for the lifetime of a task chat session."""

    def __init__(
        self,
        *,
        task_id: UUID,
        user_id: int,
        agent_name: str,
        initial_prompt: str,
        options: ClaudeAgentOptions,
        post_turn_callback: PostTurnCallback | None = None,
        idle_timeout_sec: float | None = None,
        hard_timeout_sec: float | None = None,
    ) -> None:
        self.task_id = task_id
        self.user_id = user_id
        self.agent_name = agent_name
        self.initial_prompt = initial_prompt
        self.options = options
        self._post_turn_callback = post_turn_callback
        self._client: ClaudeSDKClient | None = None
        self._turn_count = 0
        self._close_requested: SessionEndReason | None = None
        self._lifecycle = Lifecycle(
            idle_timeout_sec=idle_timeout_sec or Lifecycle.idle_timeout_sec,
            hard_timeout_sec=hard_timeout_sec or Lifecycle.hard_timeout_sec,
        )
        self._logger = structlog.get_logger("clyde.chat_session").bind(
            task_id=str(task_id), user_id=user_id, agent=agent_name
        )

    @property
    def turn_count(self) -> int:
        return self._turn_count

    def request_close(self, reason: SessionEndReason = SessionEndReason.USER_CLOSED) -> None:
        """Ask the session to wind down gracefully after the current turn.

        Safe to call from any coroutine (e.g. the WS handler when the user
        sends ``close_session``). The session itself decides exactly when
        to honour it — between turns, never mid-message.
        """
        self._close_requested = reason
        self._logger.info("chat_session.close_requested", reason=reason.value)

    async def run(self) -> SessionEndReason:
        """Main entry — opens the SDK client and drives the turn loop until
        a close reason is reached. Returns the reason."""
        self._client = ClaudeSDKClient(options=self.options)
        await self._client.connect()
        self._logger.info("chat_session.started")

        try:
            # First turn: the original task description.
            await self._run_one_turn(self.initial_prompt, is_initial=True)

            # Subsequent turns: pull from the Redis input queue.
            while self._close_requested is None:
                expired = self._lifecycle.expired()
                if expired is not None:
                    return await self._end(
                        SessionEndReason.IDLE_TIMEOUT if expired == "idle"
                        else SessionEndReason.HARD_TIMEOUT
                    )

                await self._set_status(TaskStatus.AWAITING_USER_MESSAGE)
                await broadcaster.publish(self.task_id, {
                    "name": WS_EVENT_AGENT_TURN_FINISHED,
                    "agent": self.agent_name,
                    "payload": {"turn": self._turn_count},
                    "occurred_at": _now_iso(),
                })

                budget = self._lifecycle.remaining_sec()
                if budget <= 0:
                    continue  # loop will catch via expired() above
                next_user_message = await task_input_queue.wait_for_message(
                    task_id=self.task_id, timeout_sec=budget,
                )
                if next_user_message is None:
                    continue  # timeout — loop re-checks expired()

                self._lifecycle.mark_user_input()
                await self._run_one_turn(next_user_message, is_initial=False)

            return await self._end(self._close_requested)
        except Exception as exc:
            self._logger.exception("chat_session.crashed", error=str(exc))
            return await self._end(SessionEndReason.ERROR, error=str(exc))
        finally:
            await self._teardown()

    async def _run_one_turn(self, user_text: str, *, is_initial: bool) -> None:
        """Send one user message, stream the agent's response, fire the
        post-turn callback."""
        if self._client is None:
            raise RuntimeError("SDK client not connected")

        await self._set_status(TaskStatus.RUNNING)
        await broadcaster.publish(self.task_id, {
            "name": WS_EVENT_AGENT_THINKING,
            "agent": self.agent_name,
            "payload": {"turn": self._turn_count + 1},
            "occurred_at": _now_iso(),
        })

        # For follow-up turns also persist the user-side message — the
        # initial prompt is already captured as the Task.description, but
        # subsequent user inputs only live in the queue otherwise.
        if not is_initial:
            await self._persist_user_message(user_text)

        await self._client.query(user_text)

        assistant_text_chunks: list[str] = []
        cost_usd = 0.0
        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in getattr(message, "content", []) or []:
                    text = getattr(block, "text", None)
                    if text:
                        assistant_text_chunks.append(text)
            elif isinstance(message, UserMessage):
                # Tool results from the SDK — already logged by hooks; we
                # don't echo them to the chat.
                continue
            elif isinstance(message, ResultMessage):
                cost_usd = float(getattr(message, "total_cost_usd", 0) or 0)
                self._logger.info(
                    "chat_session.turn_finished",
                    turn=self._turn_count + 1,
                    cost_usd=cost_usd,
                )

        self._turn_count += 1
        assistant_text = "\n".join(assistant_text_chunks).strip()

        if assistant_text:
            await self._persist_agent_message(assistant_text)

        result = TurnResult(
            turn_index=self._turn_count,
            assistant_text=assistant_text,
            cost_usd=cost_usd,
            result_length=len(assistant_text),
        )
        if self._post_turn_callback is not None:
            try:
                await self._post_turn_callback(self, result)
            except Exception:
                # Callback failures (e.g. publish failed) must not kill the
                # whole session — log, broadcast, keep going.
                self._logger.exception("chat_session.post_turn_callback_failed")

    async def _end(
        self, reason: SessionEndReason, *, error: str | None = None
    ) -> SessionEndReason:
        """Persist final status + broadcast the appropriate close event."""
        if reason in (SessionEndReason.IDLE_TIMEOUT, SessionEndReason.HARD_TIMEOUT):
            await broadcaster.publish(self.task_id, {
                "name": WS_EVENT_SESSION_TIMED_OUT,
                "agent": None,
                "payload": {"reason": reason.value},
                "occurred_at": _now_iso(),
            })
            await self._set_status(TaskStatus.COMPLETED)
        elif reason == SessionEndReason.USER_CLOSED:
            await broadcaster.publish(self.task_id, {
                "name": WS_EVENT_SESSION_CLOSED,
                "agent": None,
                "payload": {"reason": reason.value},
                "occurred_at": _now_iso(),
            })
            await self._set_status(TaskStatus.COMPLETED)
        else:  # ERROR
            await self._set_status(TaskStatus.FAILED, error_message=error)

        self._logger.info("chat_session.ended", reason=reason.value)
        return reason

    async def _teardown(self) -> None:
        """Close the SDK client and clean up Redis state."""
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                self._logger.exception("chat_session.disconnect_failed")
            self._client = None
        try:
            await task_input_queue.clear(task_id=self.task_id)
        except Exception:
            self._logger.exception("chat_session.input_queue_clear_failed")
        await broadcaster.close_task(self.task_id)

    async def _persist_user_message(self, content: str) -> None:
        async with db.session_scope() as session:
            await TaskMessageRepository(session).create(
                user_id=self.user_id,
                task_id=self.task_id,
                role=MessageRole.USER,
                kind=MessageKind.CHAT,
                content=content,
            )

    async def _persist_agent_message(self, content: str) -> None:
        async with db.session_scope() as session:
            await TaskMessageRepository(session).create(
                user_id=self.user_id,
                task_id=self.task_id,
                role=MessageRole.AGENT,
                kind=MessageKind.CHAT,
                content=content,
                author=self.agent_name,
            )
        await broadcaster.publish(self.task_id, {
            "name": WS_EVENT_AGENT_MESSAGE,
            "agent": self.agent_name,
            "payload": {"content": content},
            "occurred_at": _now_iso(),
        })

    async def _set_status(
        self, status: TaskStatus, *, error_message: str | None = None
    ) -> None:
        async with db.session_scope() as session:
            repo = TaskRepository(session)
            task = await repo.get(user_id=self.user_id, task_id=self.task_id)
            await repo.update_status(
                task=task, status=status, error_message=error_message
            )
        await broadcaster.publish(self.task_id, {
            "name": WS_EVENT_TASK_STATUS_CHANGED,
            "agent": None,
            "payload": {"status": status.value},
            "occurred_at": _now_iso(),
        })


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
