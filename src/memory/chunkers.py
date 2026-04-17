"""Source code chunking strategies for semantic indexing.

Python files are split along AST boundaries (functions, classes) so each
chunk carries a self-contained unit of meaning. Other languages fall back
to a sliding-window approach that respects a token budget with overlap so
context is not lost at chunk boundaries.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field

import structlog

from src.config.constants import CODE_CHUNK_TOKEN_OVERLAP, CODE_CHUNK_TOKEN_TARGET

logger = structlog.get_logger("clyde.memory.chunkers")

# Rough approximation: 1 token is about 4 characters. Good enough for
# deciding where to cut a chunk; the embedding model handles the real
# tokenisation internally.
_CHARS_PER_TOKEN = 4


@dataclass
class CodeChunkData:
    """A single chunk produced by the code chunker.

    Carries enough metadata for the caller to persist it alongside the
    embedding without needing to re-parse the file.
    """

    path: str
    start_line: int
    end_line: int
    kind: str  # function | class | module | block
    symbol: str | None
    content: str


class CodeChunker:
    """Splits source files into embedding-friendly pieces.

    Uses Python's ast module for .py files so chunks align with logical
    boundaries. For everything else, a sliding window with token overlap
    ensures no chunk is too large for the embedding model.
    """

    def __init__(
        self,
        *,
        target_tokens: int = CODE_CHUNK_TOKEN_TARGET,
        overlap_tokens: int = CODE_CHUNK_TOKEN_OVERLAP,
    ) -> None:
        self._target_chars = target_tokens * _CHARS_PER_TOKEN
        self._overlap_chars = overlap_tokens * _CHARS_PER_TOKEN

    def chunk_file(self, *, content: str, file_path: str) -> list[CodeChunkData]:
        """Split a single file into chunks tagged with their origin metadata."""
        if not content.strip():
            return []

        _, ext = os.path.splitext(file_path)
        if ext == ".py":
            try:
                chunks = self._chunk_python(content)
            except SyntaxError:
                logger.debug("chunker.python_syntax_error", path=file_path)
                chunks = self._chunk_generic(content)
        else:
            chunks = self._chunk_generic(content)

        # Tag every chunk with the file path.
        for chunk in chunks:
            chunk.path = file_path
        return chunks

    def _chunk_python(self, content: str) -> list[CodeChunkData]:
        """AST-based splitting that keeps functions and classes intact."""
        tree = ast.parse(content)
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        # Collect the line ranges of top-level definitions.
        spans: list[tuple[int, int, str, str | None]] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                spans.append((
                    node.lineno,
                    node.end_lineno or node.lineno,
                    "function",
                    node.name,
                ))
            elif isinstance(node, ast.ClassDef):
                spans.append((
                    node.lineno,
                    node.end_lineno or node.lineno,
                    "class",
                    node.name,
                ))

        spans.sort(key=lambda s: s[0])

        chunks: list[CodeChunkData] = []
        covered_up_to = 0  # 0-indexed line number of last covered line

        for start, end, kind, symbol in spans:
            # Lines between the previous span and this one are "module" chunks.
            if start - 1 > covered_up_to:
                module_content = "".join(lines[covered_up_to : start - 1])
                if module_content.strip():
                    chunks.append(CodeChunkData(
                        path="",
                        start_line=covered_up_to + 1,
                        end_line=start - 1,
                        kind="module",
                        symbol=None,
                        content=module_content,
                    ))

            span_content = "".join(lines[start - 1 : end])
            chunks.append(CodeChunkData(
                path="",
                start_line=start,
                end_line=end,
                kind=kind,
                symbol=symbol,
                content=span_content,
            ))
            covered_up_to = end

        # Trailing module-level code after the last definition.
        if covered_up_to < total_lines:
            tail = "".join(lines[covered_up_to:])
            if tail.strip():
                chunks.append(CodeChunkData(
                    path="",
                    start_line=covered_up_to + 1,
                    end_line=total_lines,
                    kind="module",
                    symbol=None,
                    content=tail,
                ))

        # If the file had no top-level definitions at all, treat it as one module chunk.
        if not chunks and content.strip():
            chunks.append(CodeChunkData(
                path="",
                start_line=1,
                end_line=total_lines,
                kind="module",
                symbol=None,
                content=content,
            ))

        return chunks

    def _chunk_generic(self, content: str) -> list[CodeChunkData]:
        """Sliding window with overlap for non-Python files."""
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)
        if not total_lines:
            return []

        chunks: list[CodeChunkData] = []
        char_pos = 0
        line_idx = 0

        while line_idx < total_lines:
            # Accumulate lines until we reach the target character count.
            chunk_start = line_idx
            chunk_chars = 0
            while line_idx < total_lines and chunk_chars < self._target_chars:
                chunk_chars += len(lines[line_idx])
                line_idx += 1

            chunk_content = "".join(lines[chunk_start:line_idx])
            if chunk_content.strip():
                chunks.append(CodeChunkData(
                    path="",
                    start_line=chunk_start + 1,
                    end_line=line_idx,
                    kind="block",
                    symbol=None,
                    content=chunk_content,
                ))

            if line_idx >= total_lines:
                break

            # Rewind by the overlap amount so the next chunk shares context.
            overlap_chars = 0
            rewind = line_idx
            while rewind > chunk_start and overlap_chars < self._overlap_chars:
                rewind -= 1
                overlap_chars += len(lines[rewind])
            line_idx = rewind

        return chunks
