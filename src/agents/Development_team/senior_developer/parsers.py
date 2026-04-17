"""Parsers for the Senior Developer agent's LLM output.

The LLM returns file changes wrapped in tagged blocks:

    <file path="src/foo.py" action="modify">
    ...full file content...
    </file>

DiffParser extracts these into structured CodeChange dicts that the agent
writes to disk and stores in state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParsedChange:
    """A single file change extracted from the LLM response."""

    path: str
    action: str
    content: str


class DiffParser:
    """Extracts structured file changes from the Senior Developer's LLM output."""

    _FILE_PATTERN = re.compile(
        r'<file\s+path="(?P<path>[^"]+)"\s+action="(?P<action>[^"]+)">'
        r"(?P<content>.*?)"
        r"</file>",
        re.DOTALL,
    )

    def parse(self, text: str) -> list[ParsedChange]:
        """Parse all <file> blocks from the LLM response text.

        Returns a list of ParsedChange objects. Content is stripped of
        leading/trailing whitespace to remove the newlines the LLM places
        around the actual code.
        """
        results: list[ParsedChange] = []
        for match in self._FILE_PATTERN.finditer(text):
            content = match.group("content")
            # Strip exactly one leading and one trailing newline if present,
            # preserving any intentional indentation in between.
            if content.startswith("\n"):
                content = content[1:]
            if content.endswith("\n"):
                content = content[:-1]

            results.append(
                ParsedChange(
                    path=match.group("path"),
                    action=match.group("action"),
                    content=content,
                )
            )
        return results
