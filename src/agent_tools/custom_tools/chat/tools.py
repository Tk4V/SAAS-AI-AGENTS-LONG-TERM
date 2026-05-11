"""Skill-tool classes for the in-process ``clyde_chat`` MCP server.

The single tool here, ``ask_user``, is the agent-side counterpart of the
human-in-the-loop approval flow used for risky tool calls. The mechanics
are identical — a ``task_approvals`` row is created, the pipeline pauses
on the Redis permission gate, the WebSocket fans the request out to the
UI — but instead of being triggered by a denied tool call this is a
first-class tool the agent invokes whenever it needs information from
the user (clarification, decision between options, missing input). The
user's free-form answer comes back as the tool result string.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID

import structlog

from src.agent_tools import permission_gate
from src.agent_tools.custom_tools.mcp_server_builder import BaseSkillTool
from src.config.constants import (
    WS_EVENT_TASK_APPROVAL_REQUESTED,
    WS_EVENT_TASK_STATUS_CHANGED,
)
from src.db.models.task import TaskStatus
from src.db.models.task_message import MessageKind, MessageRole
from src.db.queries.task_approval_query import TaskApprovalRepository
from src.db.queries.task_message_query import TaskMessageRepository
from src.db.queries.task_query import TaskRepository
from src.db.session import db
from src.utils.broadcaster import broadcaster

ASK_USER_TOOL_NAME = "ask_user"


class AskUserTool(BaseSkillTool):
    """Block until the user answers a free-form question.

    Use this whenever the agent needs information that is not in the task
    description, the repo, or any other tool: choosing between equally
    valid approaches, confirming a destructive intent, asking for missing
    credentials or business context. The pipeline pauses and the question
    is shown in the chat UI; the tool returns whatever the user types.
    """

    name: ClassVar[str] = ASK_USER_TOOL_NAME
    description: ClassVar[str] = (
        "Pause the task and ask the user a free-form question. Use this "
        "when you genuinely need information from the user that you "
        "cannot obtain from the task description, the repository, or "
        "your other tools — for example to choose between approaches, "
        "confirm a risky intent, or fill in missing context. Do not use "
        "it for things you can figure out yourself. The tool blocks "
        "until the user replies and returns their answer as plain text. "
        "If the user declines to answer the result will be the literal "
        "string 'USER_SKIPPED'."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "question": str,
        "context": str,
    }

    def __init__(self, *, task_id: UUID, user_id: int, agent_name: str) -> None:
        self._task_id = task_id
        self._user_id = user_id
        self._agent_name = agent_name
        self._logger = structlog.get_logger("clyde.tool.ask_user").bind(
            task_id=str(task_id), agent=agent_name
        )

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        question = str(args.get("question") or "").strip()
        context = str(args.get("context") or "").strip()
        if not question:
            return {"content": [{"type": "text", "text": "ERROR: question is required"}]}

        tool_input = {"question": question, "context": context}

        async with db.session_scope() as session:
            approval = await TaskApprovalRepository(session).create(
                user_id=self._user_id,
                task_id=self._task_id,
                tool_name=ASK_USER_TOOL_NAME,
                tool_input=tool_input,
            )
            approval_id = approval.id
            await TaskMessageRepository(session).create(
                user_id=self._user_id,
                task_id=self._task_id,
                role=MessageRole.AGENT,
                kind=MessageKind.APPROVAL_REQUEST,
                content=question,
                author=self._agent_name,
                meta={
                    "approval_id": str(approval_id),
                    "tool_name": ASK_USER_TOOL_NAME,
                    "context": context,
                },
            )

        async with db.session_scope() as session:
            task_repo = TaskRepository(session)
            task = await task_repo.get(user_id=self._user_id, task_id=self._task_id)
            await task_repo.update_status(task=task, status=TaskStatus.AWAITING_APPROVAL)

        # Open the wake subscription before the WS notification reaches the UI,
        # otherwise a fast user could resolve before we start listening.
        await permission_gate.register(task_id=self._task_id, approval_id=approval_id)

        await broadcaster.publish(self._task_id, {
            "name": WS_EVENT_TASK_APPROVAL_REQUESTED,
            "agent": self._agent_name,
            "payload": {
                "approval_id": str(approval_id),
                "tool_name": ASK_USER_TOOL_NAME,
                "tool_input": tool_input,
            },
            "occurred_at": datetime.now(UTC).isoformat(),
        })
        await broadcaster.publish(self._task_id, {
            "name": WS_EVENT_TASK_STATUS_CHANGED, "agent": None,
            "payload": {"status": TaskStatus.AWAITING_APPROVAL.value},
            "occurred_at": datetime.now(UTC).isoformat(),
        })

        self._logger.info("ask_user.waiting", approval_id=str(approval_id))

        approved, payload = await permission_gate.wait_for_decision(
            approval_id=approval_id
        )
        await permission_gate.cleanup(approval_id=approval_id)

        async with db.session_scope() as session:
            task_repo = TaskRepository(session)
            task = await task_repo.get(user_id=self._user_id, task_id=self._task_id)
            await task_repo.update_status(task=task, status=TaskStatus.RUNNING)

        await broadcaster.publish(self._task_id, {
            "name": WS_EVENT_TASK_STATUS_CHANGED, "agent": None,
            "payload": {"status": TaskStatus.RUNNING.value},
            "occurred_at": datetime.now(UTC).isoformat(),
        })

        # For ask_user, "approved=False" means the user explicitly skipped
        # the question. The agent should treat that as a signal to proceed
        # with its best guess rather than crash.
        if not approved:
            self._logger.info("ask_user.skipped", approval_id=str(approval_id))
            return {"content": [{"type": "text", "text": "USER_SKIPPED"}]}

        answer = str((payload or {}).get("text") or "").strip()
        if not answer:
            answer = "USER_SKIPPED"
        self._logger.info("ask_user.answered", approval_id=str(approval_id))
        return {"content": [{"type": "text", "text": answer}]}
