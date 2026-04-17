"""Tests for the CodeChunker AST-based and generic chunking strategies.

Verifies that Python files are split along function/class boundaries, that
syntax errors gracefully fall back to generic windowed chunking, and that
generic chunks respect the overlap parameter.
"""

from __future__ import annotations

from src.memory.chunkers import CodeChunker


class TestPythonChunking:
    async def test_python_splits_by_function(self):
        source = (
            "import os\n"
            "\n"
            "def foo():\n"
            "    return 1\n"
            "\n"
            "def bar():\n"
            "    return 2\n"
        )
        chunker = CodeChunker()
        chunks = chunker.chunk_file(content=source, file_path="example.py")

        function_chunks = [c for c in chunks if c.kind == "function"]
        assert len(function_chunks) == 2
        assert function_chunks[0].symbol == "foo"
        assert function_chunks[1].symbol == "bar"

    async def test_python_splits_by_class(self):
        source = (
            "class Dog:\n"
            "    def bark(self):\n"
            '        return "woof"\n'
            "\n"
            "class Cat:\n"
            "    def meow(self):\n"
            '        return "meow"\n'
        )
        chunker = CodeChunker()
        chunks = chunker.chunk_file(content=source, file_path="animals.py")

        class_chunks = [c for c in chunks if c.kind == "class"]
        assert len(class_chunks) == 2
        assert class_chunks[0].symbol == "Dog"
        assert class_chunks[1].symbol == "Cat"

    async def test_module_level_code_captured(self):
        """Imports and top-level assignments should end up in module chunks."""
        source = (
            "import os\n"
            "import sys\n"
            "\n"
            "VERSION = '1.0'\n"
            "\n"
            "def main():\n"
            "    pass\n"
        )
        chunker = CodeChunker()
        chunks = chunker.chunk_file(content=source, file_path="app.py")

        module_chunks = [c for c in chunks if c.kind == "module"]
        assert len(module_chunks) >= 1
        # The module chunk should contain the import statements
        assert any("import os" in c.content for c in module_chunks)


class TestFallbackChunking:
    async def test_syntax_error_falls_back_to_generic(self):
        """Invalid Python should not crash; it should use the generic chunker."""
        bad_python = "def broken(\n  this is not valid python at all!\n"
        chunker = CodeChunker()
        chunks = chunker.chunk_file(content=bad_python, file_path="broken.py")

        # Should still produce at least one chunk
        assert len(chunks) >= 1
        # Generic chunker produces "block" kind
        assert all(c.kind == "block" for c in chunks)

    async def test_non_python_uses_generic(self):
        """JavaScript (or any non-.py file) always goes through the generic path."""
        js_code = "function hello() {\n  console.log('hi');\n}\n" * 5
        chunker = CodeChunker()
        chunks = chunker.chunk_file(content=js_code, file_path="app.js")

        assert len(chunks) >= 1
        assert all(c.kind == "block" for c in chunks)


class TestGenericChunking:
    async def test_generic_chunks_respect_overlap(self):
        """When a file produces multiple chunks, they should share some lines."""
        # Use a very small target to force multiple chunks
        chunker = CodeChunker(target_tokens=10, overlap_tokens=3)

        # Each line is ~20 chars -> ~5 tokens. Target is 10 tokens = 2 lines.
        content = "\n".join(f"line number {i:03d}" for i in range(20)) + "\n"
        chunks = chunker.chunk_file(content=content, file_path="data.txt")

        assert len(chunks) > 1, "Should produce multiple chunks with small target"

        # Verify overlap: last lines of chunk N should appear in chunk N+1
        for i in range(len(chunks) - 1):
            current_lines = chunks[i].content.strip().splitlines()
            next_lines = chunks[i + 1].content.strip().splitlines()
            # At least one line from the tail of current should be in next
            overlap_found = any(line in next_lines for line in current_lines[-3:])
            assert overlap_found, f"No overlap between chunk {i} and {i + 1}"

    async def test_empty_content_returns_empty(self):
        chunker = CodeChunker()
        chunks = chunker.chunk_file(content="", file_path="empty.py")
        assert chunks == []

    async def test_whitespace_only_returns_empty(self):
        chunker = CodeChunker()
        chunks = chunker.chunk_file(content="   \n\n  ", file_path="blank.py")
        assert chunks == []
