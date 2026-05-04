"""Database access for the credentials table."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.credential import Credential, CredentialKind
from src.utils.exceptions import NotFoundError


class CredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        kind: CredentialKind,
        label: str,
        encrypted_payload: str,
        preview: str,
        metadata: dict[str, Any] | None = None,
    ) -> Credential:
        credential = Credential(
            user_id=user_id,
            kind=kind,
            label=label,
            encrypted_payload=encrypted_payload,
            preview=preview,
            metadata_json=metadata or {},
        )
        self._session.add(credential)
        await self._session.flush()
        return credential

    async def get(
        self,
        *,
        user_id: int,
        credential_id: UUID,
        include_deleted: bool = False,
    ) -> Credential:
        credential = await self._find(
            user_id=user_id,
            credential_id=credential_id,
            include_deleted=include_deleted,
        )
        if credential is None:
            raise NotFoundError(f"Credential {credential_id} was not found.")
        return credential

    async def list_for_user(
        self,
        *,
        user_id: int,
        offset: int,
        limit: int,
        kind: CredentialKind | None = None,
    ) -> tuple[list[Credential], int]:
        filters = [
            Credential.user_id == user_id,
            Credential.deleted_at.is_(None),
        ]
        if kind is not None:
            filters.append(Credential.kind == kind)
        base = select(Credential).where(*filters)
        rows = (
            await self._session.execute(
                base.order_by(Credential.created_at.desc()).offset(offset).limit(limit)
            )
        ).scalars().all()
        total = await self._session.scalar(
            select(func.count(Credential.id)).where(*filters)
        )
        return list(rows), int(total or 0)

    async def soft_delete(
        self,
        *,
        user_id: int,
        credential_id: UUID,
    ) -> Credential:
        credential = await self.get(user_id=user_id, credential_id=credential_id)
        credential.deleted_at = datetime.now(UTC)
        await self._session.flush()
        return credential

    async def find_active_oauth_for_provider(
        self,
        *,
        user_id: int,
        provider: str,
    ) -> Credential | None:
        """Return the most recent active OAuth credential bound to ``provider``.

        Filters by ``kind=oauth`` and matches the provider value stored in the
        non-secret ``metadata_json`` column. Soft-deleted rows are excluded.
        """
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB

        stmt = (
            select(Credential)
            .where(
                Credential.user_id == user_id,
                Credential.kind == CredentialKind.OAUTH,
                Credential.deleted_at.is_(None),
                cast(Credential.metadata_json, JSONB)["provider"].astext == provider,
            )
            .order_by(Credential.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_active_oauth_for_user(
        self,
        *,
        user_id: int,
    ) -> list[Credential]:
        """Return all active OAuth credentials for the user."""
        stmt = (
            select(Credential)
            .where(
                Credential.user_id == user_id,
                Credential.kind == CredentialKind.OAUTH,
                Credential.deleted_at.is_(None),
            )
            .order_by(Credential.created_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_active_bearer_with_provider(
        self,
        *,
        user_id: int,
    ) -> list[Credential]:
        """Return active BEARER credentials that carry a ``provider`` key in
        ``metadata_json``.

        These are non-OAuth integrations (e.g. AWS IAM credentials) that
        expose an MCP server via the backend proxy. The ``provider`` value
        is matched against ``ProviderCatalog`` to look up the MCP factory.
        """
        from sqlalchemy.dialects.postgresql import JSONB

        stmt = (
            select(Credential)
            .where(
                Credential.user_id == user_id,
                Credential.kind == CredentialKind.BEARER,
                Credential.deleted_at.is_(None),
                Credential.metadata_json.cast(JSONB)["provider"].astext.isnot(None),
            )
            .order_by(Credential.created_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def _find(
        self,
        *,
        user_id: int,
        credential_id: UUID,
        include_deleted: bool,
    ) -> Credential | None:
        stmt = select(Credential).where(
            Credential.id == credential_id,
            Credential.user_id == user_id,
        )
        if not include_deleted:
            stmt = stmt.where(Credential.deleted_at.is_(None))
        return (await self._session.execute(stmt)).scalar_one_or_none()
