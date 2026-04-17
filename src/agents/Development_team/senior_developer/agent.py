"""Senior Developer agent — implements changes one file at a time.

Instead of producing all file changes in a single LLM call (which leads to
hallucinated rewrites and out-of-scope modifications), this agent processes
each file from the Architect's plan individually. For every file it reads the
current content from disk, sends a focused prompt describing only that one
change, and writes the result back.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from src.agents.base import BaseAgent
from src.agents.development_team.senior_developer.prompts import (
    PER_FILE_CREATE_TEMPLATE,
    PER_FILE_PROMPT_TEMPLATE,
    REVIEW_FEEDBACK_ADDENDUM,
    SYSTEM_PROMPT,
)
from src.common.exceptions import PipelineError
from src.engine.registry import AgentRegistry
from src.tools import toolbox
from src.tools.llm.gateway import ChatMessage, LLMGateway

if TYPE_CHECKING:
    from src.engine.state import TaskState


class SeniorDeveloperAgent(BaseAgent):
    name = "senior_developer"
    role = "Senior Developer"

    def __init__(self, *, llm: LLMGateway | None = None) -> None:
        super().__init__()
        self._llm = llm or toolbox.llm

    async def execute(self, state: "TaskState") -> dict[str, Any]:
        description = state.get("description") or ""
        plan = state.get("plan") or {}
        repos = state.get("repos") or []
        review_feedback = state.get("review_feedback") or ""

        if not plan:
            raise PipelineError("Senior Developer invoked without a plan.")
        if not repos:
            raise PipelineError("Senior Developer invoked without repositories.")

        repo_paths: dict[str, Path] = {
            r["name"]: Path(r["local_path"])
            for r in repos
            if r.get("name") and r.get("local_path")
        }

        diffs: dict[str, list[dict[str, Any]]] = {}
        files_changed = 0

        for repo_entry in plan.get("repos", []):
            repo_name = repo_entry.get("name", "")
            repo_path = repo_paths.get(repo_name)
            if repo_path is None:
                self.logger.warning("senior_developer.unknown_repo", repo=repo_name)
                continue

            for change in repo_entry.get("changes", []):
                file_path = change.get("file", "")
                action = change.get("action", "modify")
                change_desc = change.get("description", "")

                if not file_path:
                    continue

                new_content = await self._process_single_file(
                    task_description=description,
                    repo_name=repo_name,
                    repo_path=repo_path,
                    file_path=file_path,
                    action=action,
                    change_description=change_desc,
                    review_feedback=review_feedback,
                )

                self._apply_change(
                    repo_path=repo_path,
                    file_path=file_path,
                    action=action,
                    content=new_content,
                )

                diffs.setdefault(repo_name, []).append({
                    "path": file_path,
                    "action": action,
                    "content": new_content,
                })
                files_changed += 1

                self.logger.info(
                    "senior_developer.file_done",
                    repo=repo_name,
                    file=file_path,
                    action=action,
                )

        event = {
            "name": "senior_developer.code_written",
            "agent": self.name,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": {"files_changed": files_changed},
        }

        return {"diffs": diffs, "events": [event]}

    async def _process_single_file(
        self,
        *,
        task_description: str,
        repo_name: str,
        repo_path: Path,
        file_path: str,
        action: str,
        change_description: str,
        review_feedback: str,
    ) -> str:
        """Make a single focused LLM call for one file change."""
        if action == "delete":
            return ""

        if action == "create":
            prompt = PER_FILE_CREATE_TEMPLATE.format(
                task_description=task_description,
                repo_name=repo_name,
                file_path=file_path,
                change_description=change_description,
            )
        else:
            current_content = self._read_file(repo_path / file_path)
            prompt = PER_FILE_PROMPT_TEMPLATE.format(
                task_description=task_description,
                repo_name=repo_name,
                file_path=file_path,
                action=action,
                change_description=change_description,
                current_content=current_content,
            )

        if review_feedback:
            prompt += REVIEW_FEEDBACK_ADDENDUM.format(review_feedback=review_feedback)

        response = await self._llm.chat(
            role="senior_developer",
            system=SYSTEM_PROMPT,
            messages=[ChatMessage(role="user", content=prompt)],
        )

        self.logger.info(
            "senior_developer.llm_call",
            file=file_path,
            model=response.model,
            tokens=response.usage.total,
        )

        return self._clean_response(response.text)

    @staticmethod
    def _read_file(path: Path) -> str:
        if not path.is_file():
            return "(file does not exist yet)"
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "(could not read file)"

    @staticmethod
    def _clean_response(raw: str) -> str:
        """Strip markdown fences the LLM sometimes wraps code in."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines)
        return text

    @staticmethod
    def _apply_change(
        *,
        repo_path: Path,
        file_path: str,
        action: str,
        content: str,
    ) -> None:
        target = repo_path / file_path
        if action == "delete":
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content + "\n", encoding="utf-8")


_logger = structlog.get_logger("clyde.agent.senior_developer")
AgentRegistry.instance().register(SeniorDeveloperAgent)
_logger.debug("senior_developer.registered")
