"""Schemas for OAuth authorize flow."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.db.models.project import ProviderKind


class OAuthStartResponse(BaseModel):
    provider: ProviderKind
    authorization_url: str = Field(
        description="URL the frontend must redirect the browser to.",
    )
