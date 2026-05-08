"""Admin-status lookup against the Django-owned ``auth_user`` table.

In dev and prod, clyde_ai and clyde_drf share a single Postgres instance
(separate logical schemas only on developer laptops), so
``auth_user.is_superuser`` is reachable as a regular SELECT. We treat the
table as a soft, read-only contract: column name and type are owned by
Django; if Django changes the schema there, this query is what breaks.

For local-dev setups where the two services point at separate databases,
the SELECT raises ``sqlalchemy.exc.ProgrammingError`` because the
``auth_user`` relation does not exist. The dependency layer catches that
and falls back to the ``ADMIN_USER_IDS`` allowlist so a developer can still
exercise ``/admin/*`` endpoints without merging local databases.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AdminLookupRepository:
    """Reads the Django ``auth_user.is_superuser`` flag for a given user id."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_superuser(self, user_id: int) -> bool:
        """Return ``True`` iff Django marks the user as a superuser.

        Raises ``sqlalchemy.exc.ProgrammingError`` when the ``auth_user``
        relation is absent (split-DB local dev). Callers are expected to
        catch and fall back to a secondary signal in that case.
        """
        result = await self._session.execute(
            text("SELECT is_superuser FROM auth_user WHERE id = :uid"),
            {"uid": user_id},
        )
        row = result.first()
        return bool(row and row[0])
