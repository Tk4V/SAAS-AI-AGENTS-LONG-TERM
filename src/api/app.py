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
    credentials_oauth_view,
    credentials_view,
    health_view,
    projects_view,
    providers_view,
    tasks_view,
    webhooks_view,
)
from src.api.websocket import task_stream_view
from src.config import Settings, get_settings
from src.db.session import db
from src.clients import clients


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
        app.include_router(projects_view.router, prefix=prefix)
        app.include_router(tasks_view.router, prefix=prefix)
        app.include_router(task_stream_view.router, prefix=prefix)
        app.include_router(credentials_view.router, prefix=prefix)
        app.include_router(credentials_oauth_view.router, prefix=prefix)
        app.include_router(providers_view.router, prefix=prefix)
        app.include_router(webhooks_view.router, prefix=prefix)

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
