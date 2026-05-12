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
from typing import Any

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
    task_approvals_view,
    task_messages_view,
    tasks_view,
    tools_view,
    webhooks_view,
)
from src.api.views.admin import subagents_admin_view, team_agents_admin_view
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
        "name": "Admin · Team Agents",
        "description": (
            "Admin-only update of pipeline agent configs (orchestrator, publisher). "
            "Edit system prompts, models, and tool lists without redeploying."
        ),
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
        app.include_router(task_approvals_view.router, prefix=prefix)
        app.include_router(task_messages_view.router, prefix=prefix)
        app.include_router(task_stream_view.router, prefix=prefix)
        app.include_router(credentials_view.router, prefix=prefix)
        app.include_router(credentials_oauth_view.router, prefix=prefix)
        app.include_router(providers_view.router, prefix=prefix)
        app.include_router(tools_view.router, prefix=prefix)
        app.include_router(subagents_view.router, prefix=prefix)
        app.include_router(subagents_admin_view.router, prefix=prefix)
        app.include_router(team_agents_admin_view.router, prefix=prefix)
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

        # Restart safeguard: any task left in a live status from a
        # previous run is orphaned — its chat-session coroutine died
        # with the old process and there's no way to resume the SDK
        # conversation. Mark them ``failed`` so the user sees a clear
        # signal instead of a zombie row sitting in the task list.
        try:
            await _mark_orphaned_chat_sessions_failed(log)
        except Exception:
            log.exception("application.orphan_sweep_failed")

        try:
            yield
        finally:
            # Drain any chat sessions that are still alive in this
            # process before tearing the rest of the stack down.
            try:
                from src.services.chat_session_service import chat_session_service
                await chat_session_service.shutdown_all(grace_sec=5.0)
            except Exception:
                log.exception("application.chat_shutdown_failed")
            await clients.dispose()
            await db.dispose()
            log.info("application.stopped")


_LIVE_STATUSES = {
    "running", "awaiting_approval", "awaiting_user_message",
    "publishing", "fixing",
}


async def _mark_orphaned_chat_sessions_failed(log: Any) -> None:
    """Reconcile in-flight task state with a fresh process.

    Two outcomes per orphan task:

    * **Resumable** — the row has a ``sdk_session_id``, so the SDK has a
      transcript persisted (S3 if a bucket is configured, local disk
      otherwise). Spawn a fresh chat-session coroutine with ``resume=...``;
      the agent picks up exactly where the previous container left off.
    * **Lost** — no ``sdk_session_id`` means the previous process died
      before the SDK client was even opened. Nothing to resume; stamp the
      row ``failed`` so it doesn't sit zombie in the user's task list.

    The choice is per-row, not per-batch, because both states can coexist
    after a deploy that happened during early-startup of some tasks.
    """
    import asyncio as _asyncio
    from sqlalchemy import text

    async with db.session_scope() as session:
        result = await session.execute(
            text(
                "SELECT id, user_id, sdk_session_id, status "
                "FROM tasks "
                "WHERE status::text = ANY(:statuses)"
            ),
            {"statuses": list(_LIVE_STATUSES)},
        )
        rows = list(result.mappings().all())

    if not rows:
        log.info("application.no_orphans")
        return

    resumable = [r for r in rows if r["sdk_session_id"] is not None]
    lost = [r for r in rows if r["sdk_session_id"] is None]

    if lost:
        async with db.session_scope() as session:
            await session.execute(
                text(
                    "UPDATE tasks "
                    "SET status='failed', "
                    "    error_message=COALESCE(error_message,'') "
                    "        || ' (lost on app restart — no SDK session)', "
                    "    updated_at=now() "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": [r["id"] for r in lost]},
            )
        log.warning("application.orphans_lost", count=len(lost))

    if resumable:
        # Spawn a resume coroutine per task. Each one builds its own
        # initial state from the task row + project metadata, then runs
        # the orchestrator just like a fresh task — except orchestrator's
        # execute() sees ``resume_session_id`` in state and tells the SDK
        # to reload the transcript instead of starting from scratch.
        for row in resumable:
            _asyncio.create_task(
                _resume_one(
                    task_id=row["id"],
                    user_id=row["user_id"],
                    sdk_session_id=row["sdk_session_id"],
                    log=log,
                ),
                name=f"resume-task-{row['id']}",
            )
        log.info("application.orphans_resuming", count=len(resumable))


async def _resume_one(*, task_id: Any, user_id: int, sdk_session_id: Any, log: Any) -> None:
    """Rebuild the initial pipeline state for one task and re-run it with
    ``resume_session_id`` so the SDK reloads its transcript instead of
    starting fresh. Failures are logged and the task is marked failed —
    a botched resume should never wedge app startup."""
    try:
        from src.agents.chat.turn_handler import build_post_turn_callback
        from src.agents.team.orchestrator_agent import OrchestratorAgent
        from src.db.queries.project_query import ProjectRepository
        from src.db.queries.task_query import TaskRepository
        from src.services.task_service import TaskService

        async with db.session_scope() as session:
            task = await TaskRepository(session).get(
                user_id=user_id, task_id=task_id,
            )
            project = await ProjectRepository(session).get(
                user_id=user_id, project_id=task.project_id,
            )

        initial_state = TaskService._build_initial_state(
            task=task, user_id=user_id, project=project,
        )
        initial_state["resume_session_id"] = str(sdk_session_id)
        initial_state["_post_turn_callback"] = build_post_turn_callback(
            task_id=task_id, user_id=user_id,
        )
        log.info("application.resume_spawning", task_id=str(task_id))
        await OrchestratorAgent()(initial_state)
    except Exception as exc:
        log.exception(
            "application.resume_failed", task_id=str(task_id), error=str(exc),
        )
        try:
            from sqlalchemy import text
            async with db.session_scope() as session:
                await session.execute(
                    text(
                        "UPDATE tasks SET status='failed', "
                        "  error_message=COALESCE(error_message,'') "
                        "    || ' (resume failed: ' || :err || ')', "
                        "  updated_at=now() "
                        "WHERE id = :id"
                    ),
                    {"id": task_id, "err": str(exc)[:200]},
                )
        except Exception:
            log.exception("application.resume_failure_update_failed")


app = Application().build()
