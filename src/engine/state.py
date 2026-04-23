"""Shape of the state that flows through the LangGraph pipeline.

Note: `from __future__ import annotations` is intentionally NOT used here.
LangGraph inspects TypedDict annotations at runtime to extract reducers.
"""

import operator
from typing import Annotated, Any, TypedDict


class RepoSnapshot(TypedDict, total=False):
    name: str
    url: str
    default_branch: str
    local_path: str
    head_commit: str
    branch: str


class CodeChange(TypedDict, total=False):
    path: str
    action: str  # "create" | "modify"


class SandboxOutcome(TypedDict, total=False):
    repo: str
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    passed: bool


class PipelineEvent(TypedDict, total=False):
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

    # Developer's output.
    context: dict[str, Any]
    diffs: dict[str, list[CodeChange]]

    # QA results.
    qa_results: dict[str, SandboxOutcome]
    qa_verdict: str
    qa_iteration: int

    # Publisher's output.
    pr_urls: dict[str, str]

    attempt: int
    error: str | None

    events: Annotated[list[PipelineEvent], operator.add]
