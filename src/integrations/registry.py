"""Process-wide registry of expensive infrastructure clients.

Building an Anthropic SDK client, an httpx connection pool, or a Fernet cipher
each costs measurable time and resources. Doing it per request would tank
latency, so ``Toolbox`` constructs each one lazily on first access and reuses
it for the rest of the process lifetime.

The singleton ``toolbox`` is what ``Application._lifespan``, ``api/dependencies.py``
and ``BaseAgent.__init__`` reach for. Tests should construct their own
``Toolbox(settings=...)`` and inject it via the agent / dependency constructor
rather than mutating the global.
"""

from __future__ import annotations

import structlog
from anthropic import AsyncAnthropic

from src.utils.crypto import TokenCipher
from src.config import Settings, get_settings
from src.integrations.git.factory import GitProviderFactory


class Toolbox:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._git_factory: GitProviderFactory | None = None
        self._cipher: TokenCipher | None = None
        self._anthropic: AsyncAnthropic | None = None
        self._logger = structlog.get_logger("clyde.toolbox")

    @property
    def settings(self) -> Settings:
        """Process-wide application settings (already resolved at startup)."""
        return self._settings

    @property
    def git(self) -> GitProviderFactory:
        if self._git_factory is None:
            self._git_factory = GitProviderFactory(settings=self._settings)
        return self._git_factory

    @property
    def cipher(self) -> TokenCipher:
        if self._cipher is None:
            self._cipher = TokenCipher(settings=self._settings)
        return self._cipher

    @property
    def anthropic(self) -> AsyncAnthropic:
        """Shared Anthropic client. Reused across agents to amortise the
        underlying httpx connection pool."""
        if self._anthropic is None:
            self._anthropic = AsyncAnthropic(
                api_key=self._settings.anthropic_api_key.get_secret_value(),
            )
        return self._anthropic

    async def dispose(self) -> None:
        if self._git_factory is not None:
            await self._git_factory.aclose()
            self._git_factory = None
        if self._anthropic is not None:
            # Anthropic SDK exposes close() as a coroutine on the async client.
            await self._anthropic.close()
            self._anthropic = None
        self._cipher = None
        self._logger.info("toolbox.disposed")


toolbox = Toolbox()
