"""File-system tools that let agents read, search, and list repo files.

Instead of dumping entire repos into prompts, agents use this toolkit to
pull only the lines they need, grep for patterns, and discover structure
on demand. Think of it as the agent's equivalent of Read/Grep/Glob in
Claude Code, but scoped to the repos checked out for the current task.

Only stdlib is used: pathlib, re. No external dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GrepMatch:
    """A single regex hit inside a file."""

    file_path: str
    line_number: int
    line_content: str
    context_before: list[str]
    context_after: list[str]



# Max matches grep will return to avoid blowing up context.
_GREP_MAX_MATCHES = 50

# Lines of context around each grep hit.
_GREP_CONTEXT_LINES = 2


class AgentToolkit:
    """Gives agents file-system tools similar to Claude Code's Read/Grep/Glob.

    Instead of dumping entire files into prompts, agents use this toolkit to
    read specific lines, search for patterns, and list files on demand.
    """

    def __init__(self, repo_paths: dict[str, Path]) -> None:
        self._repo_paths = repo_paths  # {repo_name: local_path}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, repo_name: str, file_path: str) -> Path:
        """Turn a repo name + relative path into an absolute path.

        Raises ValueError if the repo isn't known or the path tries to
        escape the repo root via `..` tricks.
        """
        root = self._repo_paths.get(repo_name)
        if root is None:
            raise ValueError(f"Unknown repository: {repo_name!r}")

        resolved = (root / file_path).resolve()

        # Prevent path traversal outside the repo root
        if not str(resolved).startswith(str(root.resolve())):
            raise ValueError(
                f"Path {file_path!r} resolves outside repo root"
            )
        return resolved

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_file(self, repo_name: str, file_path: str) -> str:
        """Read entire file content. Returns the text or raises."""
        target = self._resolve(repo_name, file_path)
        return target.read_text(encoding="utf-8", errors="replace")

    def read_lines(
        self, repo_name: str, file_path: str, start: int, end: int
    ) -> str:
        """Read specific line range (1-based, inclusive).

        `start` and `end` are clamped so out-of-range values won't crash.
        """
        content = self.read_file(repo_name, file_path)
        lines = content.splitlines()

        # Clamp to valid range
        start = max(1, start)
        end = min(len(lines), end)

        selected = lines[start - 1 : end]
        return "\n".join(selected)

    def grep(
        self,
        repo_name: str,
        pattern: str,
        file_glob: str = "**/*",
    ) -> list[GrepMatch]:
        """Search for a regex pattern in files. Returns matches with context.

        At most 50 matches are returned to keep things manageable.
        """
        root = self._repo_paths.get(repo_name)
        if root is None:
            raise ValueError(f"Unknown repository: {repo_name!r}")

        root = root.resolve()
        compiled = re.compile(pattern)
        matches: list[GrepMatch] = []

        for path in sorted(root.glob(file_glob)):
            if not path.is_file():
                continue

            # Skip binary / unreadable files silently
            try:
                text = path.read_text(encoding="utf-8", errors="strict")
            except (UnicodeDecodeError, OSError):
                continue

            lines = text.splitlines()

            for idx, line in enumerate(lines):
                if compiled.search(line):
                    # Grab surrounding context
                    ctx_start = max(0, idx - _GREP_CONTEXT_LINES)
                    ctx_end = min(len(lines), idx + _GREP_CONTEXT_LINES + 1)

                    matches.append(
                        GrepMatch(
                            file_path=str(path.relative_to(root)),
                            line_number=idx + 1,
                            line_content=line,
                            context_before=lines[ctx_start:idx],
                            context_after=lines[idx + 1 : ctx_end],
                        )
                    )

                    if len(matches) >= _GREP_MAX_MATCHES:
                        return matches

        return matches

    def list_files(
        self, repo_name: str, pattern: str = "**/*"
    ) -> list[str]:
        """List files matching a glob pattern, relative to repo root."""
        root = self._repo_paths.get(repo_name)
        if root is None:
            raise ValueError(f"Unknown repository: {repo_name!r}")

        root = root.resolve()
        result: list[str] = []

        for path in sorted(root.glob(pattern)):
            if path.is_file():
                result.append(str(path.relative_to(root)))

        return result
