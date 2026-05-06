"""Application bootstrap.

`Application` builds a fully configured FastAPI instance: logging, middleware,
exception handlers, lifespan and routers all live as methods on the class so
that overriding individual pieces in tests is straightforward. Production code
imports the module-level `app`, which is built from default settings.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.errors import ExceptionHandlerRegistry
from src.api.middleware import RequestContextMiddleware
from src.api.views import (
    agents_view,
    credentials_oauth_view,
    credentials_view,
    health_view,
    mcp_aws_proxy_view,
    projects_view,
    providers_view,
    subagents_view,
    tasks_view,
    tools_view,
    webhooks_view,
)
from src.api.views.admin import subagents_admin_view
from src.api.websocket import task_stream_view
from src.config import Settings, get_settings
from src.db.session import db
from src.clients import clients


_OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": "Agents",
        "description": (
            "Per-user orchestrator agents. **Workflow:** `GET /subagents` to "
            "see what is available, then `POST /agents` with one or more "
            "subagent ids. Listing, editing, and deleting your own agents "
            "live here too. The first agent you create becomes the default "
            "automatically."
        ),
    },
    {
        "name": "Agent Subagents",
        "description": (
            "Attach or detach subagents on one of your existing agents. "
            "Attaching auto-copies the admin's MCP defaults for that "
            "subagent so it works out of the box."
        ),
    },
    {
        "name": "Agent MCPs",
        "description": (
            "Override the MCP integrations one subagent has inside one of "
            "your agents. Use these endpoints to enable AWS but disable "
            "Azure for `cloud-fixer` in your `DevOps` agent, for example."
        ),
    },
    {
        "name": "Subagent Catalog",
        "description": (
            "Read-only browse of every subagent available platform-wide. "
            "Returns the `id` you need for `POST /agents` and friends."
        ),
    },
    {
        "name": "Tasks",
        "description": "Submit work to one of your agents and track its lifecycle.",
    },
    {
        "name": "Projects",
        "description": "Group repositories under a project so tasks can target them.",
    },
    {
        "name": "Credentials",
        "description": "OAuth tokens for the integrations your subagents talk to (GitHub, Jira, …).",
    },
    {
        "name": "Tools",
        "description": "MCP tool catalog used by the existing tools view.",
    },
    {
        "name": "Admin · Subagents",
        "description": (
            "Admin-only CRUD over the global subagent catalog. Requires the "
            "`is_admin` JWT claim or membership in the `ADMIN_USER_IDS` "
            "settings allowlist."
        ),
    },
    {
        "name": "Admin · System Tools",
        "description": "Admin-only listing of built-in SDK tools (Read, Edit, Bash variants, …).",
    },
    {
        "name": "Admin · MCP Servers",
        "description": "Admin-only listing of MCP server configurations.",
    },
    {
        "name": "Providers",
        "description": "Public catalog of integration providers shown in the connect flow.",
    },
    {
        "name": "MCP Proxies",
        "description": "Internal MCP proxy endpoints (AWS auth ping, etc.).",
    },
    {
        "name": "Webhooks",
        "description": "Incoming webhooks from third parties (GitHub status events).",
    },
    {
        "name": "Health",
        "description": "Liveness / readiness probes.",
    },
]


class Application:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def build(self) -> FastAPI:
        self._configure_logging()
        app = FastAPI(
            title=self._settings.app_name,
            version=self._settings.app_version,
            debug=self._settings.debug,
            docs_url=f"{self._settings.api_prefix}/docs",
            redoc_url=f"{self._settings.api_prefix}/redoc",
            openapi_url=f"{self._settings.api_prefix}/openapi.json",
            openapi_tags=_OPENAPI_TAGS,
            lifespan=self._lifespan,
        )
        self._configure_middleware(app)
        self._register_exception_handlers(app)
        self._register_routers(app)
        return app

    def _configure_logging(self) -> None:
        logging.basicConfig(level=self._settings.log_level, format="%(message)s")
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, self._settings.log_level)
            ),
            cache_logger_on_first_use=True,
        )

    def _configure_middleware(self, app: FastAPI) -> None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=self._settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        app.add_middleware(RequestContextMiddleware)

    def _register_exception_handlers(self, app: FastAPI) -> None:
        ExceptionHandlerRegistry().register(app)

    def _register_routers(self, app: FastAPI) -> None:
        prefix = self._settings.api_prefix
        app.include_router(health_view.router, prefix=prefix)
        app.include_router(agents_view.router, prefix=prefix)
        app.include_router(projects_view.router, prefix=prefix)
        app.include_router(tasks_view.router, prefix=prefix)
        app.include_router(task_stream_view.router, prefix=prefix)
        app.include_router(credentials_view.router, prefix=prefix)
        app.include_router(credentials_oauth_view.router, prefix=prefix)
        app.include_router(providers_view.router, prefix=prefix)
        app.include_router(tools_view.router, prefix=prefix)
        app.include_router(subagents_view.router, prefix=prefix)
        app.include_router(subagents_admin_view.router, prefix=prefix)
        app.include_router(webhooks_view.router, prefix=prefix)
        app.include_router(mcp_aws_proxy_view.router, prefix=prefix)

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        log = structlog.get_logger("clyde.lifespan")
        log.info(
            "application.starting",
            env=self._settings.app_env,
            version=self._settings.app_version,
        )
        try:
            yield
        finally:
            await clients.dispose()
            await db.dispose()
            log.info("application.stopped")


app = Application().build()
