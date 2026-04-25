"""Robust JSON extraction from LLM responses.

Claude often wraps JSON output in markdown code fences or adds prose
before/after the block, even when the prompt says not to. This parser
handles all known variations so each agent does not need its own logic.
"""

from __future__ import annotations

import json
from typing import Any

from src.utils.exceptions import PipelineError


class LLMJsonParser:
    """Extracts and parses JSON objects from raw LLM text responses.

    Tries progressively more aggressive extraction strategies until
    a valid JSON object is found or all strategies are exhausted.
    """

    @classmethod
    def parse(cls, raw: str, *, agent: str = "LLM") -> dict[str, Any]:
        """Extract and parse a JSON object from a potentially messy LLM response.

        Strategies applied in order:
        1. Strip markdown code fences and parse the cleaned text.
        2. Find the outermost ``{`` and ``}`` in the cleaned text.
        3. Find the outermost ``{`` and ``}`` in the original text.

        Args:
            raw: The raw text response from the LLM.
            agent: Agent name for error messages.

        Returns:
            The parsed JSON object as a dictionary.

        Raises:
            PipelineError: If no valid JSON object can be extracted.
        """
        cleaned = cls._strip_code_fences(raw.strip())

        result = cls._try_parse(cleaned)
        if result is not None:
            return result

        result = cls._extract_outermost_braces(cleaned)
        if result is not None:
            return result

        result = cls._extract_outermost_braces(raw.strip())
        if result is not None:
            return result

        raise PipelineError(
            f"{agent} returned a response that could not be parsed as JSON.",
            details={"raw": raw[:500]},
        )

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Remove markdown code fences (```json ... ``` or ``` ... ```)."""
        if not text.startswith("```"):
            return text
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()

    @staticmethod
    def _try_parse(text: str) -> dict[str, Any] | None:
        """Attempt to parse text as JSON, returning None on failure."""
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    @classmethod
    def _extract_outermost_braces(cls, text: str) -> dict[str, Any] | None:
        """Find the first ``{`` and last ``}`` and try to parse that substring."""
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return cls._try_parse(text[start : end + 1])
        return None
