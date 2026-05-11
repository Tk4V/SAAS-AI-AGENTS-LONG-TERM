"""Project-wide constants not configurable via environment variables."""

from __future__ import annotations

from typing import Final

WS_EVENT_TASK_STATUS_CHANGED: Final = "task.status_changed"
WS_EVENT_PIPELINE_FAILED: Final = "pipeline.failed"
WS_EVENT_TASK_APPROVAL_REQUESTED: Final = "task.approval_requested"
WS_EVENT_AGENT_MESSAGE: Final = "agent.message"
WS_EVENT_USER_MESSAGE: Final = "user.message"
WS_EVENT_APPROVAL_RESOLVED: Final = "task.approval_resolved"

WS_EVENT_AGENT_THINKING: Final = "agent.thinking"
WS_EVENT_AGENT_TURN_FINISHED: Final = "agent.turn_finished"
WS_EVENT_SESSION_TIMED_OUT: Final = "session.timed_out"
WS_EVENT_SESSION_CLOSED: Final = "session.closed"

WS_EVENT_PUBLISH_STARTED: Final = "publish.started"
WS_EVENT_PUBLISH_FINISHED: Final = "publish.finished"
WS_EVENT_PUBLISH_FAILED: Final = "publish.failed"

WS_INBOUND_APPROVAL_RESPONSE: Final = "approval_response"
WS_INBOUND_CHAT_MESSAGE: Final = "chat_message"
WS_INBOUND_CLOSE_SESSION: Final = "close_session"
WS_INBOUND_PING: Final = "ping"

# Chat session lifecycle tuning. Defaults conservative — Anthropic prefix
# cache TTL is ~5 min so any idle longer than that loses the cache; a
# longer idle window is still useful (user can come back, lose some
# cache, continue). Hard cap stops genuinely abandoned sessions.
CHAT_SESSION_IDLE_TIMEOUT_SEC: Final = 30 * 60
CHAT_SESSION_HARD_TIMEOUT_SEC: Final = 4 * 60 * 60
