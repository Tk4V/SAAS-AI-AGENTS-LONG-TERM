"""Robust JSON extraction from LLM responses.

Claude often wraps JSON output in markdown code fences or adds a brief
sentence before/after the block, even when the prompt says not to. This
module handles all known variations so each agent does not need its own
parsing logic.
"""

from __future__ import annotations

import json

from src.common.exceptions import PipelineError


def parse_llm_json(raw: str, *, agent: str = "LLM") -> dict:
    """Extract and parse a JSON object from a potentially messy LLM response.

    Tries progressively more aggressive extraction strategies:
    1. Strip markdown fences and parse the whole text.
    2. Find the first `{` and last `}` and parse that substring.
    """
    text = raw.strip()

    # Strategy 1: strip code fences (```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    result = _try_parse(text)
    if result is not None:
        return result

    # Strategy 2: find outermost braces
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        result = _try_parse(text[start : end + 1])
        if result is not None:
            return result

    # Strategy 3: same on original (pre-fence-strip) text
    original = raw.strip()
    start = original.find("{")
    end = original.rfind("}")
    if start != -1 and end > start:
        result = _try_parse(original[start : end + 1])
        if result is not None:
            return result

    raise PipelineError(
        f"{agent} returned a response that could not be parsed as JSON.",
        details={"raw": raw[:500]},
    )


def _try_parse(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None
