"""HTTP views for credential CRUD."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.api.dependencies import CredentialServiceDep, CurrentUserDep
from src.api.schemas.common_schemas import Page, PaginationParams
from src.api.schemas.credential_schemas import (
    BearerCredentialCreate,
    CredentialRead,
)
from src.db.models.credential import CredentialKind

router = APIRouter(prefix="/credentials", tags=["credentials"])


class CredentialView:
    """Create, list, fetch and soft-delete user credentials."""

    @staticmethod
    @router.post("", response_model=CredentialRead, status_code=status.HTTP_201_CREATED)
    async def create_bearer(
        payload: BearerCredentialCreate,
        user: CurrentUserDep,
        service: CredentialServiceDep,
    ) -> CredentialRead:
        """Store a new bearer credential. Token is encrypted at rest."""
        credential = await service.create(
            user_id=user.id,
            kind=CredentialKind.BEARER,
            label=payload.label,
            payload_raw=payload.payload.model_dump(),
            metadata_raw=payload.metadata.model_dump(),
        )
        return CredentialRead.from_orm(credential)

    @staticmethod
    @router.get("", response_model=Page[CredentialRead])
    async def list(
        user: CurrentUserDep,
        service: CredentialServiceDep,
        pagination: Annotated[PaginationParams, Depends()],
    ) -> Page[CredentialRead]:
        """Return active credentials for the current user. No tokens leaked."""
        rows, total = await service.list(
            user_id=user.id, offset=pagination.offset, limit=pagination.limit
        )
        return Page[CredentialRead](
            items=[CredentialRead.from_orm(row) for row in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    @staticmethod
    @router.get("/{credential_id}", response_model=CredentialRead)
    async def get(
        credential_id: UUID,
        user: CurrentUserDep,
        service: CredentialServiceDep,
    ) -> CredentialRead:
        """Fetch a single credential by id. Token never returned."""
        credential = await service.get(user_id=user.id, credential_id=credential_id)
        return CredentialRead.from_orm(credential)

    @staticmethod
    @router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete(
        credential_id: UUID,
        user: CurrentUserDep,
        service: CredentialServiceDep,
    ) -> None:
        """Soft-delete a credential. Audit history remains."""
        await service.delete(user_id=user.id, credential_id=credential_id)
