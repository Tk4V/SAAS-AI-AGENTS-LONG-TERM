"""Result type returned by every `SandboxRunner`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    timed_out: bool
    image: str
    command: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict:
        """JSON-friendly representation suitable for storing in TaskState."""
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_sec": self.duration_sec,
            "timed_out": self.timed_out,
            "image": self.image,
            "command": list(self.command),
            "passed": self.passed,
        }
