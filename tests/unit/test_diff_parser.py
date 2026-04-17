"""Tests for the DiffParser that extracts <file> tags from LLM output.

The Senior Developer agent wraps code changes in XML-like <file> tags. The
parser must handle single files, multiple files, and gracefully return an
empty list when there are no tags at all.
"""

from __future__ import annotations

from src.agents.development_team.senior_developer.parsers import DiffParser


class TestDiffParser:
    async def test_parses_file_tags(self):
        llm_output = (
            'Here is the change:\n\n'
            '<file path="src/main.py" action="modify">\n'
            'def hello():\n'
            '    return "world"\n'
            '</file>\n'
        )
        parser = DiffParser()
        changes = parser.parse(llm_output)

        assert len(changes) == 1
        assert changes[0].path == "src/main.py"
        assert changes[0].action == "modify"
        assert "def hello" in changes[0].content

    async def test_handles_multiple_files(self):
        llm_output = (
            '<file path="src/a.py" action="create">\n'
            'print("a")\n'
            '</file>\n'
            '<file path="src/b.py" action="modify">\n'
            'print("b")\n'
            '</file>\n'
            '<file path="src/c.py" action="delete">\n'
            '</file>\n'
        )
        parser = DiffParser()
        changes = parser.parse(llm_output)

        assert len(changes) == 3
        assert changes[0].path == "src/a.py"
        assert changes[0].action == "create"
        assert changes[1].path == "src/b.py"
        assert changes[2].path == "src/c.py"
        assert changes[2].action == "delete"

    async def test_handles_empty_input(self):
        """When the LLM returns no file tags, the parser should return []."""
        parser = DiffParser()

        assert parser.parse("") == []
        assert parser.parse("No changes needed.") == []
        assert parser.parse("I looked at the code but everything is fine.") == []

    async def test_strips_surrounding_newlines(self):
        """Leading and trailing newlines around content should be stripped."""
        llm_output = (
            '<file path="x.py" action="create">\n'
            'content here\n'
            '</file>'
        )
        parser = DiffParser()
        changes = parser.parse(llm_output)

        assert len(changes) == 1
        # Should not start or end with \n
        assert not changes[0].content.startswith("\n")
        assert not changes[0].content.endswith("\n")
