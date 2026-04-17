"""Pipeline executor and LangGraph checkpoint lifecycle.

`CheckpointerManager` owns the psycopg connection pool used by
`AsyncPostgresSaver`. The saver persists every step the graph takes, so a task
can resume after a process restart or after a CI webhook arrives hours later.

`PipelineExecutor` wraps a compiled graph and turns its `astream` output into
an async iterator that the WebSocket handler can consume directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from src.common.exceptions import AppError
from src.config import Settings, get_settings
from src.engine.graph_builder import PipelineGraphBuilder
from src.engine.registry import AgentRegistry

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from src.engine.state import TaskState


class CheckpointerManager:
    """Sets up and tears down the LangGraph Postgres checkpointer.

    Constructed once per process. The pool size is intentionally small — most
    of our DB traffic flows through the SQLAlchemy engine; the checkpointer
    only writes a row per agent step.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._pool: AsyncConnectionPool | None = None
        self._saver: AsyncPostgresSaver | None = None
        self._logger = structlog.get_logger("clyde.checkpointer")

    def _build_conninfo(self) -> str:
        """Build a keyword-value libpq connection string.

        Uses psycopg.conninfo.make_conninfo which properly escapes special
        characters in passwords without percent-encoding issues.
        """
        from psycopg.conninfo import make_conninfo

        params: dict[str, object] = {
            "host": self._settings.db_host,
            "port": self._settings.db_port,
            "dbname": self._settings.db_name,
            "user": self._settings.db_user,
            "password": self._settings.db_password.get_secret_value(),
            "connect_timeout": 10,
        }
        if self._settings.db_ssl and self._settings.db_ssl != "disable":
            params["sslmode"] = self._settings.db_ssl
        return make_conninfo(**params)

    async def setup(self) -> None:
        """Try to connect the checkpointer pool.

        If the connection fails (network issue, RDS not reachable), the app
        still starts. The pool will retry in the background and become
        available later. Pipeline execution will fail with a clear error
        until the pool is ready.
        """
        if self._saver is not None:
            return

        conninfo = self._build_conninfo()
        self._logger.info(
            "checkpointer.connecting",
            host=self._settings.db_host,
            dbname=self._settings.db_name,
        )

        try:
            self._pool = AsyncConnectionPool(
                conninfo=conninfo,
                min_size=1,
                max_size=4,
                open=False,
                reconnect_timeout=5,
                kwargs={"autocommit": True, "prepare_threshold": 0},
            )
            await self._pool.open(wait=True, timeout=15.0)
            self._saver = AsyncPostgresSaver(self._pool)
            await self._saver.setup()
            self._logger.info("checkpointer.ready")
        except Exception as exc:
            self._logger.warning(
                "checkpointer.setup_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                hint="The app will start without the checkpointer. "
                     "Pipeline execution will fail until the DB is reachable.",
            )
            if self._pool is not None:
                await self._pool.close()
                self._pool = None
            self._saver = None

    @property
    def saver(self) -> AsyncPostgresSaver:
        if self._saver is None:
            raise RuntimeError(
                "CheckpointerManager.setup() must be called before accessing the saver.",
            )
        return self._saver

    async def dispose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._saver = None
            self._logger.info("checkpointer.disposed")


class PipelineExecutor:
    """Streams events from a compiled LangGraph state graph.

    The compiled graph is built once at startup with the saver from
    `CheckpointerManager`. Per-task isolation is achieved by passing a unique
    `thread_id` (the task UUID) into LangGraph's runtime config.
    """

    def __init__(self, graph: "CompiledStateGraph") -> None:
        self._graph = graph
        self._logger = structlog.get_logger("clyde.executor")

    async def stream(
        self,
        *,
        task_id: UUID,
        initial_state: "TaskState",
    ) -> AsyncIterator[dict[str, Any]]:
        config = {"configurable": {"thread_id": str(task_id)}}
        self._logger.info("pipeline.started", task_id=str(task_id))
        try:
            async for event in self._graph.astream(initial_state, config=config):
                yield event
        finally:
            self._logger.info("pipeline.finished", task_id=str(task_id))

    async def get_state(self, *, task_id: UUID) -> dict[str, Any]:
        """Fetch the latest persisted state for a task without resuming it."""
        config = {"configurable": {"thread_id": str(task_id)}}
        snapshot = await self._graph.aget_state(config)
        return dict(snapshot.values) if snapshot else {}

    async def resume(
        self,
        *,
        task_id: UUID,
        patch: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Continue a paused task, optionally patching state before resuming.

        Used by the GitHub webhook handler when the DevOps agent has fetched
        CI logs and is ready to attempt a fix.
        """
        config = {"configurable": {"thread_id": str(task_id)}}
        if patch:
            await self._graph.aupdate_state(config, patch)
        self._logger.info("pipeline.resumed", task_id=str(task_id))
        async for event in self._graph.astream(None, config=config):
            yield event


class EngineNotReadyError(AppError):
    """Raised when something asks for the executor before runtime.setup() ran."""

    code = "engine_not_ready"
    http_status = 503


class EngineRuntime:
    """Single object that owns the engine's process-wide collaborators.

    Lifecycle:
        await runtime.setup()    # called from Application._lifespan
        runtime.executor         # lazy-builds the compiled graph on first use
        await runtime.dispose()  # tears down the checkpointer pool
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._checkpointer = CheckpointerManager(self._settings)
        self._registry = AgentRegistry.instance()
        self._builder = PipelineGraphBuilder(self._registry)
        self._executor: PipelineExecutor | None = None
        self._is_ready = False
        self._logger = structlog.get_logger("clyde.runtime")

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    @property
    def checkpointer(self) -> CheckpointerManager:
        return self._checkpointer

    @property
    def executor(self) -> PipelineExecutor:
        if not self._is_ready:
            raise EngineNotReadyError(
                "EngineRuntime.setup() must be called before requesting the executor.",
            )
        if self._executor is None:
            graph = self._builder.build_default(self._checkpointer.saver)
            self._executor = PipelineExecutor(graph)
        return self._executor

    async def setup(self) -> None:
        await self._checkpointer.setup()
        self._registry.autoload()
        self._is_ready = True
        self._logger.info(
            "runtime.ready",
            agents=sorted(self._registry.all().keys()),
        )

    async def dispose(self) -> None:
        await self._checkpointer.dispose()
        self._executor = None
        self._is_ready = False


runtime = EngineRuntime()
