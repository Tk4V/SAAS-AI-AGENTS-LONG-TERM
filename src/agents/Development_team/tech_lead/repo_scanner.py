"""Walks a cloned repository and produces a structured snapshot.

The output is a `RepoInsight` containing a directory tree and a curated set
of file contents. The scanner enforces budgets (file count, per-file size,
total bytes) so the result fits inside the LLM context window.

Filtering is intentionally conservative: we skip vendored directories
(`node_modules`, `.venv`, etc.), binaries, lockfiles, and anything bigger
than `max_file_bytes`. The aim is to give the LLM enough signal to plan a
change without flooding it with framework noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".github",
        ".idea",
        ".vscode",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        ".cache",
        "vendor",
        "tmp",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)

EXCLUDED_FILE_SUFFIXES = frozenset(
    {
        ".lock",
        ".log",
        ".sqlite",
        ".sqlite3",
        ".db",
        ".pyc",
        ".pyo",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".bin",
        ".jar",
        ".class",
        ".o",
        ".a",
        ".ico",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp3",
        ".mp4",
        ".mov",
    }
)

EXCLUDED_FILE_NAMES = frozenset(
    {
        ".DS_Store",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "Cargo.lock",
        "Pipfile.lock",
        "uv.lock",
    }
)


@dataclass(frozen=True, slots=True)
class FileSnippet:
    path: str
    bytes_total: int
    content: str


@dataclass(frozen=True, slots=True)
class RepoInsight:
    name: str
    root: Path
    file_tree: list[str]
    snippets: list[FileSnippet]
    total_files_seen: int
    files_skipped: int
    truncated: bool = field(default=False)


class RepoScanner:
    def __init__(
        self,
        *,
        max_files: int = 60,
        max_file_bytes: int = 60_000,
        max_total_bytes: int = 600_000,
        tree_depth: int = 6,
    ) -> None:
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes
        self._max_total_bytes = max_total_bytes
        self._tree_depth = tree_depth

    def scan(self, *, name: str, root: Path) -> RepoInsight:
        root = root.resolve()
        tree = self._build_tree(root)
        snippets, total_seen, skipped, truncated = self._collect_snippets(root)
        return RepoInsight(
            name=name,
            root=root,
            file_tree=tree,
            snippets=snippets,
            total_files_seen=total_seen,
            files_skipped=skipped,
            truncated=truncated,
        )

    def _build_tree(self, root: Path) -> list[str]:
        lines: list[str] = []

        def walk(directory: Path, prefix: str, depth: int) -> None:
            if depth > self._tree_depth:
                return
            try:
                entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
            except OSError:
                return
            for entry in entries:
                if entry.is_dir() and entry.name in EXCLUDED_DIRS:
                    continue
                rel = f"{prefix}{entry.name}"
                lines.append(rel + ("/" if entry.is_dir() else ""))
                if entry.is_dir():
                    walk(entry, prefix=rel + "/", depth=depth + 1)

        walk(root, prefix="", depth=0)
        return lines

    def _collect_snippets(
        self,
        root: Path,
    ) -> tuple[list[FileSnippet], int, int, bool]:
        snippets: list[FileSnippet] = []
        total_seen = 0
        skipped = 0
        consumed_bytes = 0
        truncated = False

        for file_path in self._iter_text_files(root):
            total_seen += 1
            if len(snippets) >= self._max_files or consumed_bytes >= self._max_total_bytes:
                truncated = True
                skipped += 1
                continue

            try:
                size = file_path.stat().st_size
            except OSError:
                skipped += 1
                continue

            if size > self._max_file_bytes:
                skipped += 1
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                skipped += 1
                continue

            snippets.append(
                FileSnippet(
                    path=str(file_path.relative_to(root)),
                    bytes_total=size,
                    content=content,
                )
            )
            consumed_bytes += size

        return snippets, total_seen, skipped, truncated

    def _iter_text_files(self, root: Path):
        priority_names = (
            "README.md",
            "README.rst",
            "README.txt",
            "pyproject.toml",
            "setup.cfg",
            "package.json",
            "Cargo.toml",
            "go.mod",
        )

        seen: set[Path] = set()

        for name in priority_names:
            candidate = root / name
            if candidate.is_file() and self._is_text_candidate(candidate):
                seen.add(candidate)
                yield candidate

        for path in sorted(root.rglob("*")):
            if path in seen:
                continue
            if not path.is_file():
                continue
            if any(part in EXCLUDED_DIRS for part in path.parts):
                continue
            if not self._is_text_candidate(path):
                continue
            seen.add(path)
            yield path

    @staticmethod
    def _is_text_candidate(path: Path) -> bool:
        if path.name in EXCLUDED_FILE_NAMES:
            return False
        if path.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
            return False
        return True
