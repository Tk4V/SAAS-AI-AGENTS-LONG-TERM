"""Abstract sandbox runner.

QA Engineer asks the runner to execute a command (typically `pytest`,
`go test`, `npm test`) inside a fresh isolated environment built from a
specific repo checkout. The runner must enforce timeout, memory and CPU
limits and isolate the network so test code cannot reach external services.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path

from src.tools.sandbox.result import SandboxResult


class SandboxRunner(ABC):
    @abstractmethod
    async def run(
        self,
        *,
        repo_path: Path,
        command: Sequence[str],
        image: str,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
    ) -> SandboxResult:
        """Execute `command` inside a sandbox with `repo_path` mounted as the workdir."""
