"""Integration tests for FastAPI HTTP endpoints.

Uses httpx AsyncClient with ASGITransport to test the real application.
Only tests that work without a database are included here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.app import Application
from src.config.settings import Settings
from tests.conftest import make_test_jwt


def _build_test_app(test_settings: Settings) -> FastAPI:
    """Build a FastAPI app with a no-op lifespan for testing."""

    @asynccontextmanager
    async def test_lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield

    base_app = Application(settings=test_settings)
    original_build = base_app.build

    def patched_build() -> FastAPI:
        app = original_build()
        app.router.lifespan_context = test_lifespan
        return app

    base_app.build = patched_build
    return base_app.build()


@pytest.fixture
def app(test_settings) -> FastAPI:
    """Test FastAPI app with engine lifespan stubbed out."""
    return _build_test_app(test_settings)


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    """Async HTTP client hitting the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealth:
    """Verify health and readiness endpoints respond correctly."""

    async def test_health_returns_ok(self, client) -> None:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "clyde-ai"


class TestAuthEnforcement:
    """Verify that protected endpoints reject unauthenticated requests."""

    async def test_create_project_requires_auth(self, client) -> None:
        response = await client.post("/api/v1/projects", json={"name": "Test"})
        assert response.status_code == 401

    async def test_list_projects_requires_auth(self, client) -> None:
        response = await client.get("/api/v1/projects")
        assert response.status_code == 401

    async def test_create_task_requires_auth(self, client) -> None:
        response = await client.post(
            "/api/v1/tasks",
            json={"project_id": str(uuid4()), "description": "test"},
        )
        assert response.status_code == 401

    async def test_list_tasks_requires_auth(self, client) -> None:
        response = await client.get("/api/v1/tasks")
        assert response.status_code == 401
