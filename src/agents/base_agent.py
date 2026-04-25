"""Base class every agent in the dev team inherits from.

Concrete agents live in `src/agents/dev_team/` and are imported
directly by the pipeline runner.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

import structlog

from src.tools.auth.token_resolver import TokenResolver


class BaseAgent(ABC):
    """Abstract base for all pipeline agents.

    Provides structured logging, a __call__ entry point with lifecycle events,
    and shared helpers like GitHub token resolution. Subclasses must define
    ``name``, ``role``, and implement ``execute``.
    """

    name: ClassVar[str]
    role: ClassVar[str]

    def __init__(self) -> None:
        """Initialise the agent with a scoped logger."""
        self._logger = structlog.get_logger(f"clyde.agent.{self.name}")
        self._token_resolver = TokenResolver()

    @property
    def logger(self) -> Any:
        """Bound structlog logger scoped to this agent's name."""
        return self._logger

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the agent, wrapping ``execute`` with start/finish lifecycle events."""
        self._logger.info(
            "agent.started",
            task_id=state.get("task_id"),
            attempt=state.get("attempt"),
        )
        try:
            result = await self.execute(state)
        except Exception as exc:
            self._logger.exception("agent.failed", error=str(exc))
            raise
        self._logger.info("agent.finished", produced_keys=list(result.keys()))
        return result

    @abstractmethod
    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Do the work and return only the keys that should change in the state."""

    async def resolve_github_token(self, *, user_id: int) -> str:
        """Fetch and decrypt the user's GitHub OAuth token."""
        return await self._token_resolver.resolve(user_id=user_id)
