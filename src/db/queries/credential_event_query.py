"""Database access for credential audit events."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.credential_event import CredentialEvent


class CredentialEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: int,
        credential_id: UUID,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> CredentialEvent:
        event = CredentialEvent(
            user_id=user_id,
            credential_id=credential_id,
            event_type=event_type,
            details=details or {},
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_for_credential(
        self,
        *,
        user_id: int,
        credential_id: UUID,
    ) -> list[CredentialEvent]:
        stmt = (
            select(CredentialEvent)
            .where(
                CredentialEvent.credential_id == credential_id,
                CredentialEvent.user_id == user_id,
            )
            .order_by(CredentialEvent.created_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())
