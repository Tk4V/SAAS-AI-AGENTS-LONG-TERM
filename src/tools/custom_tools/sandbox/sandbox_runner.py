"""Abstract sandbox runner.

Executes a command (typically `pytest`, `go test`, `npm test`) inside a
fresh isolated environment built from a specific repo checkout. The runner
enforces timeout, memory and CPU limits and isolates the network so test
code cannot reach external services.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path

from src.tools.custom_tools.sandbox.sandbox_result import SandboxResult


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
