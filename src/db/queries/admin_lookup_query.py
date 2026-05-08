"""Admin-status lookup against the Django-owned ``accounts_user`` table.

clyde_drf overrides Django's default ``AUTH_USER_MODEL`` with a custom
model whose table is ``accounts_user`` (not ``auth_user``). In dev and
prod, both services share a single Postgres database (``public`` schema),
so ``accounts_user.is_superuser`` is reachable as a regular SELECT. We
treat the table as a soft, read-only contract: column name and type are
owned by Django; if Django renames the model or column, this query is
what breaks.

For local-dev setups where the two services point at separate databases,
the SELECT raises ``sqlalchemy.exc.ProgrammingError`` because the
``accounts_user`` relation does not exist. The dependency layer catches
that and falls back to the ``ADMIN_USER_IDS`` allowlist so a developer
can still exercise ``/admin/*`` endpoints without merging local databases.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AdminLookupRepository:
    """Reads the Django ``accounts_user.is_superuser`` flag for a given user id."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_superuser(self, user_id: int) -> bool:
        """Return ``True`` iff Django marks the user as a superuser.

        Raises ``sqlalchemy.exc.ProgrammingError`` when the ``accounts_user``
        relation is absent (split-DB local dev). Callers are expected to
        catch and fall back to a secondary signal in that case.
        """
        result = await self._session.execute(
            text("SELECT is_superuser FROM accounts_user WHERE id = :uid"),
            {"uid": user_id},
        )
        row = result.first()
        return bool(row and row[0])
