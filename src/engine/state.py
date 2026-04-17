"""Shape of the state that flows through the LangGraph pipeline.

`TaskState` is a TypedDict because LangGraph serialises it through its
checkpointer; only JSON-friendly types belong here. UUIDs are stored as
strings for that reason.

All keys are optional (`total=False`) — every agent only produces the slice
of state it owns and LangGraph merges those diffs into the running state.
The `events` key is concatenated rather than overwritten so the executor can
stream a chronological log to the WebSocket client.

Note: `from __future__ import annotations` is intentionally NOT used here.
LangGraph inspects TypedDict annotations at runtime to extract reducers
(like `operator.add` on the `events` field). Stringified annotations would
break that introspection.
"""

import operator
from typing import Annotated, Any, TypedDict


class RepoSnapshot(TypedDict, total=False):
    """One git repository as seen by the pipeline."""

    name: str
    url: str
    default_branch: str
    local_path: str
    head_commit: str
    branch: str


class CodeChange(TypedDict, total=False):
    """A single file edit produced by Senior Developer."""

    path: str
    action: str  # "create" | "modify" | "delete"
    diff: str


class SandboxOutcome(TypedDict, total=False):
    """Result of running tests in the sandbox for one repo."""

    repo: str
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    passed: bool


class PipelineEvent(TypedDict, total=False):
    """Streamed to the WebSocket client and persisted in `Task.state`."""

    name: str
    agent: str | None
    payload: dict[str, Any]
    occurred_at: str


class TaskState(TypedDict, total=False):
    task_id: str
    user_id: int
    project_id: str
    description: str

    repos: list[RepoSnapshot]

    # Tech Lead's analysis: relevant files, summaries, cross-repo context.
    context: dict[str, Any]

    # Architect's plan: ordered list of changes per repo plus rationale.
    plan: dict[str, Any]

    # Senior Developer's diffs keyed by repo name.
    diffs: dict[str, list[CodeChange]]

    review_verdict: str
    review_feedback: str
    review_iteration: int

    qa_results: dict[str, SandboxOutcome]
    qa_verdict: str
    qa_iteration: int

    pr_urls: dict[str, str]

    # CI fix loop counter. Incremented by the DevOps agent after a webhook fail.
    attempt: int

    error: str | None

    events: Annotated[list[PipelineEvent], operator.add]
