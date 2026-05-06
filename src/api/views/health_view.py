"""Liveness and readiness endpoints.

`/health` is a cheap liveness check used by ECS/ALB. `/ready` issues a single
SELECT against Postgres so that traffic is not routed to a node that cannot
talk to its database.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import text

from src.api.dependencies import SessionDep
from src.config import get_settings

router = APIRouter(tags=["Health"])


class HealthView:
    """Health and readiness check endpoints."""

    @staticmethod
    @router.get("/health", status_code=status.HTTP_200_OK)
    async def health() -> dict[str, str]:
        """Return basic service info for load balancer health checks."""
        settings = get_settings()
        return {
            "status": "ok",
            "service": settings.app_name,
            "version": settings.app_version,
            "env": settings.app_env,
        }

    @staticmethod
    @router.get("/ready", status_code=status.HTTP_200_OK)
    async def ready(session: SessionDep) -> dict[str, str]:
        """Verify database connectivity with a lightweight query."""
        await session.execute(text("SELECT 1"))
        return {"status": "ready"}
