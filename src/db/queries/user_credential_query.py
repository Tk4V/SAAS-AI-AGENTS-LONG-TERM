"""Database access for the per-user OAuth credentials table."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.utils.exceptions import NotFoundError
from src.db.models.project import GitProviderKind
from src.db.models.user_credential import UserOAuthCredential


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
    ) -> UserOAuthCredential:
        """Insert or replace the credential for the given (user, provider)."""
        existing = await self._find(user_id=user_id, provider=provider)
        if existing is not None:
            existing.token_encrypted = token_encrypted
            existing.scopes = scopes
            await self._session.flush()
            return existing

        credential = UserOAuthCredential(
            user_id=user_id,
            provider=provider,
            token_encrypted=token_encrypted,
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
