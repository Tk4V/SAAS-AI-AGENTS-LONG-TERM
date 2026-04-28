"""Database access for the per-user OAuth credentials table."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import GitProviderKind
from src.db.models.user_credential import UserOAuthCredential
from src.utils.exceptions import NotFoundError


class UserOAuthCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        user_id: int,
        provider: GitProviderKind,
        token_encrypted: str,
        scopes: str,
        refresh_token_encrypted: str | None = None,
        expires_at: datetime | None = None,
        provider_account_id: str | None = None,
        account_label: str | None = None,
        raw_metadata: dict[str, Any] | None = None,
    ) -> UserOAuthCredential:
        """Insert or replace the credential for the given (user, provider).

        New OAuth fields default to None / empty so that the GitHub OAuth App
        flow (which has no refresh token, no expiry, no account discovery)
        keeps working unchanged.
        """
        existing = await self._find(user_id=user_id, provider=provider)
        if existing is not None:
            existing.token_encrypted = token_encrypted
            existing.refresh_token_encrypted = refresh_token_encrypted
            existing.expires_at = expires_at
            existing.provider_account_id = provider_account_id
            existing.account_label = account_label
            existing.raw_metadata = raw_metadata or {}
            existing.scopes = scopes
            await self._session.flush()
            return existing

        credential = UserOAuthCredential(
            user_id=user_id,
            provider=provider,
            token_encrypted=token_encrypted,
            refresh_token_encrypted=refresh_token_encrypted,
            expires_at=expires_at,
            provider_account_id=provider_account_id,
            account_label=account_label,
            raw_metadata=raw_metadata or {},
            scopes=scopes,
        )
        self._session.add(credential)
        await self._session.flush()
        return credential

    async def get(
        self,
        *,
        user_id: int,
        provider: GitProviderKind,
    ) -> UserOAuthCredential:
        credential = await self._find(user_id=user_id, provider=provider)
        if credential is None:
            raise NotFoundError(
                f"No {provider.value} credential connected for this user.",
            )
        return credential

    async def list_for_user(self, *, user_id: int) -> list[UserOAuthCredential]:
        stmt = (
            select(UserOAuthCredential)
            .where(UserOAuthCredential.user_id == user_id)
            .order_by(UserOAuthCredential.granted_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def delete(
        self,
        *,
        user_id: int,
        provider: GitProviderKind,
    ) -> None:
        credential = await self.get(user_id=user_id, provider=provider)
        await self._session.delete(credential)

    async def _find(
        self,
        *,
        user_id: int,
        provider: GitProviderKind,
    ) -> UserOAuthCredential | None:
        stmt = select(UserOAuthCredential).where(
            UserOAuthCredential.user_id == user_id,
            UserOAuthCredential.provider == provider,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
