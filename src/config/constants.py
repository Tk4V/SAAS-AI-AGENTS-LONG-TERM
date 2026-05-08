"""Project-wide constants not configurable via environment variables."""

from __future__ import annotations

from typing import Final

WS_EVENT_TASK_STATUS_CHANGED: Final = "task.status_changed"
WS_EVENT_PIPELINE_FAILED: Final = "pipeline.failed"
WS_EVENT_TASK_APPROVAL_REQUESTED: Final = "task.approval_requested"
