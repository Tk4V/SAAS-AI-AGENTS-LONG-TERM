"""Base class every agent in the dev team inherits from.

Agents are the unit of work the engine schedules. Each agent receives the
current task state, performs its slice of work and returns a state diff that
LangGraph merges back into the global state.

Concrete agents live in `src/agents/development_team/<role>/agent.py` and
register themselves with `AgentRegistry`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from src.common.crypto import TokenCipher
from src.db.models.project import GitProviderKind
from src.db.queries.user_credential_queries import UserOAuthCredentialRepository
from src.db.session import Database, db

if TYPE_CHECKING:
    from src.engine.state import TaskState


class BaseAgent(ABC):
    """Abstract base for every dev-team agent.

    Subclasses must define `name` (machine identifier used by the registry and
    LangGraph nodes) and `role` (human-readable label shown to the user).
    """

    name: ClassVar[str]
    role: ClassVar[str]

    def __init__(self) -> None:
        self._logger = structlog.get_logger(f"clyde.agent.{self.name}")

    @property
    def logger(self) -> Any:
        return self._logger

    async def __call__(self, state: "TaskState") -> dict[str, Any]:
        """LangGraph nodes are plain callables; this delegates to `execute`.

        The wrapper exists so that subclasses only override `execute`, while
        cross-cutting concerns (logging, event emission, error handling) live
        here in one place.
        """
        self._logger.info(
            "agent.started",
            task_id=state.get("task_id"),
            attempt=state.get("attempt"),
        )
        try:
            diff = await self.execute(state)
        except Exception as exc:
            self._logger.exception("agent.failed", error=str(exc))
            raise
        self._logger.info("agent.finished", produced_keys=list(diff.keys()))
        return diff

    @abstractmethod
    async def execute(self, state: "TaskState") -> dict[str, Any]:
        """Do the work and return only the keys that should change in the state."""

    async def resolve_github_token(
        self,
        *,
        user_id: int,
        database: Database | None = None,
        cipher: TokenCipher | None = None,
    ) -> str:
        """Fetch and decrypt the user's GitHub OAuth token from the database.

        Shared by every agent that needs to interact with GitHub (Tech Lead for
        cloning, Release Manager for pushing, DevOps for fetching CI logs). The
        token lives only in the local variable scope and never enters the
        LangGraph state.
        """
        from src.tools import toolbox

        _db = database or db
        _cipher = cipher or toolbox.cipher
        async with _db.session_scope() as session:
            credentials = UserOAuthCredentialRepository(session)
            credential = await credentials.get(
                user_id=user_id, provider=GitProviderKind.GITHUB
            )
            return _cipher.decrypt(credential.token_encrypted)
