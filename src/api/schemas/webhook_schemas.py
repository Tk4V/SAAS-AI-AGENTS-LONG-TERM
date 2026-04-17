"""Pydantic models for GitHub webhook payloads.

We only care about the `workflow_run` event right now. The models use
`extra="ignore"` so the dozens of fields GitHub sends that we don't need
won't break parsing or clutter our logs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RepoData(BaseModel):
    """Minimal repository information from the webhook payload."""

    model_config = ConfigDict(extra="ignore")

    full_name: str  # "owner/repo"
    html_url: str


class WorkflowRunData(BaseModel):
    """Subset of the nested `workflow_run` object we actually use."""

    model_config = ConfigDict(extra="ignore")

    id: int
    status: str  # "completed"
    conclusion: str | None = None  # "success" | "failure" | "cancelled" | etc.
    head_branch: str
    head_sha: str
    html_url: str
    repository: RepoData


class GitHubWorkflowRunPayload(BaseModel):
    """Top-level shape of the `workflow_run` webhook event."""

    model_config = ConfigDict(extra="ignore")

    action: str  # "completed", "requested", "in_progress"
    workflow_run: WorkflowRunData
