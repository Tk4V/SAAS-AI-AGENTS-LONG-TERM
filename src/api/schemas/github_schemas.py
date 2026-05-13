"""Pydantic schemas for the GitHub integration endpoints.

Shapes mirror exactly what ``GitHubApiClient.list_repos`` already produces,
so the route layer can pass dicts straight into the model without any
field-by-field renaming.
"""

from __future__ import annotations

from pydantic import BaseModel


class GitHubRepoRead(BaseModel):
    """One repository visible to the authenticated user."""

    full_name: str
    url: str
    default_branch: str
    private: bool
    description: str


class GitHubReposList(BaseModel):
    items: list[GitHubRepoRead]
