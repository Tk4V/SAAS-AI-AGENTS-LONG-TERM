"""Pydantic schemas for the credentials resource."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.credentials.payloads.bearer import BearerMetadata, BearerPayload
from src.db.models.credential import CredentialKind

if TYPE_CHECKING:
    from src.db.models.credential import Credential


class BearerCredentialCreate(BaseModel):
    """Request body for creating a bearer credential.

    ``kind`` is fixed to ``"bearer"`` so the same endpoint can later accept
    a discriminated union when more kinds are supported. Today only bearer
    is wired up.
    """

    kind: Literal["bearer"] = "bearer"
    label: str = Field(min_length=1, max_length=255)
    payload: BearerPayload
    metadata: BearerMetadata = Field(default_factory=BearerMetadata)


class CredentialRead(BaseModel):
    """Single credential representation returned to the API client.

    Never includes the decrypted token; ``preview`` is the only secret-derived
    field and is built to be safe to display.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: CredentialKind
    label: str
    preview: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, credential: Credential) -> CredentialRead:
        return cls(
            id=credential.id,
            kind=credential.kind,
            label=credential.label,
            preview=credential.preview,
            metadata=credential.metadata_json,
            created_at=credential.created_at,
            updated_at=credential.updated_at,
        )
