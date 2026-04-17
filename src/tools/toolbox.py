"""Process-wide registry of expensive tool clients.

Building an Anthropic SDK client, a Docker SDK client, an httpx connection
pool or a Fernet cipher each costs measurable time and resources. Doing it
per request would tank latency, so `Toolbox` constructs each one lazily on
first access and reuses it for the rest of the process lifetime.

The singleton `toolbox` is what `Application._lifespan` and `deps.py` reach
for. Tests should construct their own `Toolbox(settings=...)` rather than
mutating the global.
"""

from __future__ import annotations

import structlog

from src.common.crypto import TokenCipher
from src.config import Settings, get_settings
from src.memory.embeddings import EmbeddingClient
from src.tools.git.factory import GitProviderFactory
from src.tools.llm.gateway import LLMGateway
from src.tools.llm.providers.anthropic import AnthropicLLMGateway
from src.tools.sandbox.runner import SandboxRunner
from src.tools.sandbox.runners.docker_runner import DockerSandboxRunner


class Toolbox:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._llm: LLMGateway | None = None
        self._git_factory: GitProviderFactory | None = None
        self._sandbox: SandboxRunner | None = None
        self._cipher: TokenCipher | None = None
        self._embedder: EmbeddingClient | None = None
        self._logger = structlog.get_logger("clyde.toolbox")

    @property
    def llm(self) -> LLMGateway:
        if self._llm is None:
            self._llm = AnthropicLLMGateway(settings=self._settings)
        return self._llm

    @property
    def git(self) -> GitProviderFactory:
        if self._git_factory is None:
            self._git_factory = GitProviderFactory(settings=self._settings)
        return self._git_factory

    @property
    def sandbox(self) -> SandboxRunner:
        if self._sandbox is None:
            self._sandbox = DockerSandboxRunner(settings=self._settings)
        return self._sandbox

    @property
    def cipher(self) -> TokenCipher:
        if self._cipher is None:
            self._cipher = TokenCipher(settings=self._settings)
        return self._cipher

    @property
    def embedder(self) -> EmbeddingClient:
        if self._embedder is None:
            self._embedder = EmbeddingClient(settings=self._settings)
        return self._embedder

    async def dispose(self) -> None:
        if self._git_factory is not None:
            await self._git_factory.aclose()
            self._git_factory = None
        self._llm = None
        self._sandbox = None
        self._cipher = None
        self._embedder = None
        self._logger.info("toolbox.disposed")


toolbox = Toolbox()
