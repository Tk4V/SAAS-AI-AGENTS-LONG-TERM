"""Minimal contract every Clyde agent fulfills.

For agents that need to launch a Claude Agent SDK session, inherit from
``SDKAgent`` (in ``sdk_agent.py``) instead — it adds the SDK configuration
contract on top of this base.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

import structlog

from src.integrations.oauth.token_resolver import TokenResolver
from src.integrations.registry import Toolbox, toolbox as default_toolbox


class BaseAgent(ABC):
    """Abstract base for every Clyde pipeline agent.

    To create a new agent:
        1. Set the class attribute ``name`` (short identifier used in logs and
           the pipeline runner — e.g. ``"publisher"``).
        2. Set the class attribute ``role`` (human-readable label — e.g.
           ``"Publisher"``).
        3. Implement ``async execute(self, state) -> dict``. Return only the
           keys the agent contributes to the pipeline state.

    The base provides for free:
        - ``self.toolbox`` — process-wide infrastructure registry (git,
          anthropic, cipher, settings). Tests pass a ``Toolbox`` instance via
          the constructor; production uses the global singleton by default.
        - ``self.logger`` — structlog logger scoped to the agent's ``name``.
        - ``__call__(state)`` — lifecycle wrapper that logs start/finish/fail
          around every ``execute`` invocation, so the pipeline can simply call
          ``await agent(state)``.
        - ``resolve_github_token(user_id)`` — helper for fetching and
          decrypting the user's stored GitHub OAuth token.

    Agents that need to drive an autonomous Claude Agent SDK loop should
    inherit from ``SDKAgent`` instead, which extends this contract with
    ``SDK_ALLOWED_TOOLS`` + ``build_mcp_servers`` + ``run_sdk_session``.
    """

    name: ClassVar[str]
    role: ClassVar[str]

    def __init__(self, *, toolbox: Toolbox | None = None) -> None:
        self._toolbox = toolbox or default_toolbox
        self._logger = structlog.get_logger(f"clyde.agent.{self.name}")
        self._token_resolver = TokenResolver(cipher=self._toolbox.cipher)

    @property
    def toolbox(self) -> Toolbox:
        """Process-wide infrastructure registry (git, anthropic, cipher, settings)."""
        return self._toolbox

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
        """Do the work and return only the keys the agent contributes to the state."""

    async def resolve_github_token(self, *, user_id: int) -> str:
        """Fetch and decrypt the user's GitHub OAuth token."""
        return await self._token_resolver.resolve(user_id=user_id)
