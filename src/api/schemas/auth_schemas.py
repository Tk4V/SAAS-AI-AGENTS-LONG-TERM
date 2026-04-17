"""Schemas for OAuth integration endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.db.models.project import GitProviderKind

if TYPE_CHECKING:
    from src.db.models.user_credential import UserOAuthCredential


class OAuthStartResponse(BaseModel):
    provider: GitProviderKind
    authorization_url: str = Field(
        description="URL the frontend must redirect the browser to.",
    )


class IntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: GitProviderKind
    scopes: list[str]
    granted_at: datetime

    @classmethod
    def from_orm(cls, credential: "UserOAuthCredential") -> "IntegrationRead":
        scope_list = [
            scope.strip()
            for scope in credential.scopes.split(",")
            if scope.strip()
        ]
        return cls(
            id=credential.id,
            provider=credential.provider,
            scopes=scope_list,
            granted_at=credential.granted_at,
        )


class IntegrationsList(BaseModel):
    items: list[IntegrationRead]


class GitRepoItem(BaseModel):
    full_name: str
    url: str
    default_branch: str
    private: bool
    description: str


class GitRepoList(BaseModel):
    items: list[GitRepoItem]
