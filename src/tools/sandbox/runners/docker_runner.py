"""Docker-based implementation of `SandboxRunner`.

Spawns a fresh container per call, mounts the repo read-write so build
artefacts can land there, applies memory and CPU caps, and disables the
network by default. The Docker SDK is synchronous so every call goes through
`asyncio.to_thread` to keep the event loop free.

In production on AWS Fargate this implementation will not work (no docker
socket); the engine will need a different `SandboxRunner` such as one backed
by E2B or Firecracker. That is intentional — picking the right runner per
environment is exactly what `SandboxRunner` exists for.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from pathlib import Path

import docker
import structlog
from docker.errors import APIError, ContainerError, DockerException, ImageNotFound

from src.common.exceptions import SandboxError
from src.config import Settings, get_settings
from src.tools.sandbox.result import SandboxResult
from src.tools.sandbox.runner import SandboxRunner


class DockerSandboxRunner(SandboxRunner):
    DEFAULT_IMAGE = "python:3.12-slim"
    WORKDIR = "/workspace"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: "docker.DockerClient | None" = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client_override = client
        self._client: "docker.DockerClient | None" = None
        self._logger = structlog.get_logger("clyde.sandbox.docker")

    def _get_client(self) -> "docker.DockerClient":
        """Connect to Docker on first use, not at construction time."""
        if self._client is None:
            self._client = self._client_override or docker.from_env()
        return self._client

    async def run(
        self,
        *,
        repo_path: Path,
        command: Sequence[str],
        image: str = "",
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
    ) -> SandboxResult:
        chosen_image = image or self.DEFAULT_IMAGE
        timeout = timeout_sec or self._settings.sandbox_timeout_sec
        command_tuple = tuple(command)

        started = time.perf_counter()
        try:
            stdout, stderr, exit_code, timed_out = await asyncio.to_thread(
                self._run_blocking,
                repo_path=repo_path,
                command=list(command_tuple),
                image=chosen_image,
                env=env or {},
                timeout=timeout,
            )
        except DockerException as exc:
            raise SandboxError(
                "Docker sandbox failed to execute the command.",
                details={"error": str(exc)},
            ) from exc

        duration = time.perf_counter() - started
        self._logger.info(
            "sandbox.run.completed",
            image=chosen_image,
            command=command_tuple,
            exit_code=exit_code,
            duration_sec=round(duration, 2),
            timed_out=timed_out,
        )
        return SandboxResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_sec=duration,
            timed_out=timed_out,
            image=chosen_image,
            command=command_tuple,
        )

    def _run_blocking(
        self,
        *,
        repo_path: Path,
        command: list[str],
        image: str,
        env: dict[str, str],
        timeout: int,
    ) -> tuple[str, str, int, bool]:
        try:
            container = self._get_client().containers.create(
                image=image,
                command=command,
                working_dir=self.WORKDIR,
                environment=env,
                volumes={
                    str(repo_path.resolve()): {
                        "bind": self.WORKDIR,
                        "mode": "rw",
                    }
                },
                network_mode=self._settings.sandbox_network,
                mem_limit=self._settings.sandbox_memory_limit,
                nano_cpus=int(self._settings.sandbox_cpu_limit * 1_000_000_000),
                detach=True,
                tty=False,
                stdin_open=False,
            )
        except ImageNotFound:
            self._get_client().images.pull(image)
            return self._run_blocking(
                repo_path=repo_path,
                command=command,
                image=image,
                env=env,
                timeout=timeout,
            )

        timed_out = False
        try:
            container.start()
            try:
                wait_result = container.wait(timeout=timeout)
                exit_code = int(wait_result.get("StatusCode", -1))
            except Exception:
                container.kill()
                wait_result = container.wait()
                exit_code = int(wait_result.get("StatusCode", -1))
                timed_out = True

            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")
        except (APIError, ContainerError) as exc:
            raise SandboxError(
                "Docker container failed during execution.",
                details={"error": str(exc)},
            ) from exc
        finally:
            try:
                container.remove(force=True)
            except APIError:
                pass

        return stdout, stderr, exit_code, timed_out
