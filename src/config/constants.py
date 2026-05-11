"""Project-wide constants not configurable via environment variables."""

from __future__ import annotations

from typing import Final

WS_EVENT_TASK_STATUS_CHANGED: Final = "task.status_changed"
WS_EVENT_PIPELINE_FAILED: Final = "pipeline.failed"
WS_EVENT_TASK_APPROVAL_REQUESTED: Final = "task.approval_requested"
WS_EVENT_AGENT_MESSAGE: Final = "agent.message"
WS_EVENT_USER_MESSAGE: Final = "user.message"
WS_EVENT_APPROVAL_RESOLVED: Final = "task.approval_resolved"

WS_INBOUND_APPROVAL_RESPONSE: Final = "approval_response"
WS_INBOUND_CHAT_MESSAGE: Final = "chat_message"
WS_INBOUND_PING: Final = "ping"
