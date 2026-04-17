"""Combines per-repo scan results into a single cross-repo context.

The merger sends one prompt to the LLM that contains the task description
and a JSON-encoded view of every repository the task is attached to. The
model returns a JSON object that downstream agents (Architect, Senior
Developer) consume directly.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from src.common.exceptions import PipelineError
from src.tools.llm.gateway import ChatMessage, LLMGateway

from src.agents.development_team.tech_lead.prompts import (
    MERGE_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
)
from src.agents.development_team.tech_lead.repo_scanner import RepoInsight


class MultiRepoContextMerger:
    """One-shot LLM call that turns repo snapshots into structured context."""

    AGENT_ROLE = "tech_lead"

    def __init__(
        self,
        llm: LLMGateway,
        *,
        max_tree_lines_per_repo: int = 200,
    ) -> None:
        self._llm = llm
        self._max_tree_lines = max_tree_lines_per_repo
        self._logger = structlog.get_logger("clyde.agent.tech_lead.merger")

    async def merge(
        self,
        *,
        task: str,
        insights: list[RepoInsight],
    ) -> dict[str, Any]:
        repositories_payload = [self._serialise_insight(insight) for insight in insights]
        prompt = MERGE_PROMPT_TEMPLATE.format(
            task=task,
            repositories=json.dumps(repositories_payload, indent=2),
        )

        response = await self._llm.chat(
            role=self.AGENT_ROLE,
            system=SYSTEM_PROMPT,
            messages=[ChatMessage(role="user", content=prompt)],
            temperature=0.2,
        )

        self._logger.info(
            "tech_lead.llm.completed",
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        from src.common.json_utils import parse_llm_json
        return parse_llm_json(response.text, agent="Tech Lead")

    def _serialise_insight(self, insight: RepoInsight) -> dict[str, Any]:
        tree = insight.file_tree[: self._max_tree_lines]
        truncated_tree = len(insight.file_tree) > self._max_tree_lines
        return {
            "name": insight.name,
            "file_tree": tree,
            "file_tree_truncated": truncated_tree,
            "snippets": [
                {"path": snippet.path, "content": snippet.content}
                for snippet in insight.snippets
            ],
            "scanner_stats": {
                "total_files_seen": insight.total_files_seen,
                "files_skipped": insight.files_skipped,
                "files_returned": len(insight.snippets),
                "scan_truncated": insight.truncated,
            },
        }

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PipelineError(
                "Tech Lead returned malformed JSON for the merged context.",
                details={"snippet": text[:500], "error": str(exc)},
            ) from exc
        if not isinstance(parsed, dict):
            raise PipelineError(
                "Tech Lead context must be a JSON object at the top level.",
                details={"got_type": type(parsed).__name__},
            )
        return parsed
