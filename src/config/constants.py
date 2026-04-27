"""Project-wide constants not configurable via environment variables."""

from __future__ import annotations

from typing import Final

CODE_CHUNK_TOKEN_TARGET: Final = 800
CODE_CHUNK_TOKEN_OVERLAP: Final = 100

EPISODIC_RECALL_TOP_K: Final = 5
SEMANTIC_RECALL_TOP_K: Final = 20

TOOL_LOOP_DEFAULT_TOKEN_BUDGET: Final = 100_000

WS_EVENT_TASK_STATUS_CHANGED: Final = "task.status_changed"
WS_EVENT_PIPELINE_FAILED: Final = "pipeline.failed"
