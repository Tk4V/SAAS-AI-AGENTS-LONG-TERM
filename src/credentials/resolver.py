"""Read path that returns decrypted credential payloads to internal callers.

Only code that genuinely needs to act on the secret value (HTTP signers,
OAuth refreshers, future tool runtimes) goes through the resolver. Each
resolution is recorded in the audit log so we can later answer "which
component touched this secret and when".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.credentials.audit import CredentialAuditor
from src.credentials.kinds import KindRegistry
from src.credentials.oauth.refresher import OAuthRefresher
from src.db.models.credential import Credential, CredentialKind
from src.db.queries.credential_query import CredentialRepository
from src.utils.crypto import TokenCipher


@dataclass(frozen=True)
class ResolvedCredential:
    """Decrypted credential ready for use by an internal caller.

    ``payload`` and ``metadata`` are pydantic models specific to the kind;
    callers narrow them by inspecting ``kind`` (or by knowing in advance
    which kind they asked for).
    """

    id: UUID
    kind: CredentialKind
    label: str
    payload: Any
    metadata: Any


class CredentialResolver:
    def __init__(
        self,
        repository: CredentialRepository,
        cipher: TokenCipher,
        kinds: KindRegistry,
        auditor: CredentialAuditor,
        oauth_refresher: OAuthRefresher | None = None,
    ) -> None:
        self._repo = repository
        self._cipher = cipher
        self._kinds = kinds
        self._auditor = auditor
        self._oauth_refresher = oauth_refresher

    async def resolve(
        self,
        *,
        user_id: int,
        credential_id: UUID,
        purpose: str | None = None,
    ) -> ResolvedCredential:
        credential = await self._repo.get(
            user_id=user_id, credential_id=credential_id
        )
        handler = self._kinds.get(credential.kind)
        plaintext = self._cipher.decrypt(credential.encrypted_payload)
        payload = handler.deserialise_payload(plaintext)
        metadata = handler.deserialise_metadata(credential.metadata_json)

        if (
            credential.kind is CredentialKind.OAUTH
            and self._oauth_refresher is not None
            and self._oauth_refresher.needs_refresh(metadata)
        ):
            payload, metadata = await self._oauth_refresher.refresh(
                credential=credential,
                payload=payload,
                metadata=metadata,
            )

        await self._auditor.resolved(
            user_id=user_id,
            credential_id=credential.id,
            details={"purpose": purpose} if purpose else None,
        )
        return ResolvedCredential(
            id=credential.id,
            kind=credential.kind,
            label=credential.label,
            payload=payload,
            metadata=metadata,
        )

    async def get_credential(
        self,
        *,
        user_id: int,
        credential_id: UUID,
    ) -> Credential:
        """Return the ORM row without decrypting the payload."""
        return await self._repo.get(
            user_id=user_id, credential_id=credential_id
        )
