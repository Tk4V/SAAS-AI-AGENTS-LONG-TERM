"""Project-wide constants that are not user-configurable via environment variables.

Anything that should be tunable in production lives in `settings.py` instead.
"""

from __future__ import annotations

from typing import Final

# Pipeline state machine. Values are persisted in the database, so changing them
# requires a data migration.
TASK_STATUS_RUNNING: Final = "running"
TASK_STATUS_AWAITING_CI: Final = "awaiting_ci"
TASK_STATUS_FIXING: Final = "fixing"
TASK_STATUS_COMPLETED: Final = "completed"
TASK_STATUS_NEEDS_HUMAN: Final = "needs_human"
TASK_STATUS_FAILED: Final = "failed"

CODE_REVIEW_APPROVE: Final = "approve"
CODE_REVIEW_REQUEST_CHANGES: Final = "request_changes"

QA_RESULT_PASS: Final = "pass"
QA_RESULT_FAIL: Final = "fail"

# Maximum size of a single code chunk passed to the embedding model. Voyage's
# voyage-3-large takes up to 32k tokens but smaller chunks give better recall.
CODE_CHUNK_TOKEN_TARGET: Final = 800
CODE_CHUNK_TOKEN_OVERLAP: Final = 100

# Maximum number of episodes returned from semantic memory recall per task.
EPISODIC_RECALL_TOP_K: Final = 5
SEMANTIC_RECALL_TOP_K: Final = 20

# How long an LLM call is allowed to run before we cancel it. Long tasks should
# stream rather than blocking on a single response.
LLM_REQUEST_TIMEOUT_SEC: Final = 120

# WebSocket event names emitted by the executor. Frontend filters on these.
WS_EVENT_AGENT_STARTED: Final = "agent.started"
WS_EVENT_AGENT_FINISHED: Final = "agent.finished"
WS_EVENT_TASK_STATUS_CHANGED: Final = "task.status_changed"
WS_EVENT_PIPELINE_FAILED: Final = "pipeline.failed"
