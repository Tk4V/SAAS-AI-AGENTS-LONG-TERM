"""High-level CRUD for credentials.

Service is the only path through which the API layer touches the credentials
table. It encrypts secret payloads before they reach the repository, builds
the redacted preview for list responses, and writes an audit event for every
state-changing operation in the same transaction.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.credentials.audit import CredentialAuditor
from src.credentials.kinds import KindRegistry
from src.db.models.credential import Credential, CredentialKind
from src.db.queries.credential_query import CredentialRepository
from src.utils.crypto import TokenCipher


class CredentialService:
    def __init__(
        self,
        repository: CredentialRepository,
        cipher: TokenCipher,
        kinds: KindRegistry,
        auditor: CredentialAuditor,
    ) -> None:
        self._repo = repository
        self._cipher = cipher
        self._kinds = kinds
        self._auditor = auditor

    async def create(
        self,
        *,
        user_id: int,
        kind: CredentialKind,
        label: str,
        payload_raw: dict[str, Any],
        metadata_raw: dict[str, Any] | None = None,
    ) -> Credential:
        handler = self._kinds.get(kind)
        payload = handler.parse_payload(payload_raw)
        metadata = handler.parse_metadata(metadata_raw)

        encrypted = self._cipher.encrypt(handler.serialise_payload(payload))
        preview = handler.build_preview(payload)

        credential = await self._repo.create(
            user_id=user_id,
            kind=kind,
            label=label.strip(),
            encrypted_payload=encrypted,
            preview=preview,
            metadata=handler.serialise_metadata(metadata),
        )
        await self._auditor.created(
            user_id=user_id,
            credential_id=credential.id,
            details={"kind": kind.value, "label": credential.label},
        )
        return credential

    async def list(
        self,
        *,
        user_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[Credential], int]:
        return await self._repo.list_for_user(
            user_id=user_id, offset=offset, limit=limit
        )

    async def get(
        self,
        *,
        user_id: int,
        credential_id: UUID,
    ) -> Credential:
        credential = await self._repo.get(
            user_id=user_id, credential_id=credential_id
        )
        await self._auditor.read(
            user_id=user_id,
            credential_id=credential.id,
        )
        return credential

    async def delete(
        self,
        *,
        user_id: int,
        credential_id: UUID,
    ) -> None:
        credential = await self._repo.soft_delete(
            user_id=user_id, credential_id=credential_id
        )
        await self._auditor.deleted(
            user_id=user_id,
            credential_id=credential.id,
            details={"kind": credential.kind.value},
        )
