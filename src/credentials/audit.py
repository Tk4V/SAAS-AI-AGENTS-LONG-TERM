"""Synchronous audit logger for credential operations.

Writes happen on the same async session as the credential mutation so the
audit trail and the credential row commit or roll back together.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.db.models.credential_event import CredentialEventType
from src.db.queries.credential_event_query import CredentialEventRepository


class CredentialAuditor:
    def __init__(self, events: CredentialEventRepository) -> None:
        self._events = events

    async def created(
        self,
        *,
        user_id: int,
        credential_id: UUID,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self._events.record(
            user_id=user_id,
            credential_id=credential_id,
            event_type=CredentialEventType.CREATED.value,
            details=details,
        )

    async def read(
        self,
        *,
        user_id: int,
        credential_id: UUID,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self._events.record(
            user_id=user_id,
            credential_id=credential_id,
            event_type=CredentialEventType.READ.value,
            details=details,
        )

    async def resolved(
        self,
        *,
        user_id: int,
        credential_id: UUID,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self._events.record(
            user_id=user_id,
            credential_id=credential_id,
            event_type=CredentialEventType.RESOLVED.value,
            details=details,
        )

    async def deleted(
        self,
        *,
        user_id: int,
        credential_id: UUID,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self._events.record(
            user_id=user_id,
            credential_id=credential_id,
            event_type=CredentialEventType.DELETED.value,
            details=details,
        )
