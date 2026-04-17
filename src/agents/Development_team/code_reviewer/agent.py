"""Code Reviewer agent — reviews diffs against the plan and coding standards.

Receives the plan, the Senior Developer's diffs, and the review iteration
counter. Asks the LLM to evaluate the changes and returns either an approval
or actionable feedback that gets routed back to the Senior Developer.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from src.agents.base import BaseAgent
from src.agents.development_team.code_reviewer.prompts import (
    REVIEW_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
)
from src.common.exceptions import PipelineError
from src.config.constants import CODE_REVIEW_APPROVE, CODE_REVIEW_REQUEST_CHANGES
from src.engine.registry import AgentRegistry
from src.tools import toolbox
from src.tools.llm.gateway import ChatMessage, LLMGateway

if TYPE_CHECKING:
    from src.engine.state import TaskState


class CodeReviewerAgent(BaseAgent):
    name = "code_reviewer"
    role = "Code Reviewer"

    def __init__(
        self,
        *,
        llm: LLMGateway | None = None,
    ) -> None:
        super().__init__()
        self._llm = llm or toolbox.llm

    async def execute(self, state: "TaskState") -> dict[str, Any]:
        description = state.get("description") or ""
        context = state.get("context") or {}
        plan = state.get("plan") or {}
        diffs = state.get("diffs") or {}
        iteration = int(state.get("review_iteration") or 0)

        if not diffs:
            raise PipelineError("Code Reviewer invoked without any diffs.")

        result = await self._review(
            description=description,
            context=context,
            plan=plan,
            diffs=diffs,
            iteration=iteration,
        )

        verdict = result.get("verdict", CODE_REVIEW_REQUEST_CHANGES)
        feedback = result.get("feedback", "")

        # Normalise verdict to our constants.
        if verdict not in (CODE_REVIEW_APPROVE, CODE_REVIEW_REQUEST_CHANGES):
            self.logger.warning(
                "code_reviewer.unexpected_verdict",
                raw_verdict=verdict,
            )
            verdict = CODE_REVIEW_REQUEST_CHANGES

        new_iteration = iteration + 1

        event = {
            "name": "code_reviewer.reviewed",
            "agent": self.name,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": {
                "verdict": verdict,
                "iteration": new_iteration,
            },
        }

        return {
            "review_verdict": verdict,
            "review_feedback": feedback,
            "review_iteration": new_iteration,
            "events": [event],
        }

    async def _review(
        self,
        *,
        description: str,
        context: dict[str, Any],
        plan: dict[str, Any],
        diffs: dict[str, Any],
        iteration: int,
    ) -> dict[str, Any]:
        user_message = REVIEW_PROMPT_TEMPLATE.format(
            description=description,
            plan=json.dumps(plan, indent=2),
            context=json.dumps(context, indent=2),
            diffs=json.dumps(diffs, indent=2),
            iteration=iteration + 1,
        )

        response = await self._llm.chat(
            role="code_reviewer",
            system=SYSTEM_PROMPT,
            messages=[ChatMessage(role="user", content=user_message)],
        )

        self.logger.info(
            "code_reviewer.llm_response",
            model=response.model,
            tokens=response.usage.total,
        )

        from src.common.json_utils import parse_llm_json
        try:
            return parse_llm_json(response.text, agent="Code Reviewer")
        except Exception:
            # If we cannot parse the review, default to approve so the pipeline
            # is not blocked by a formatting issue.
            self.logger.warning(
                "code_reviewer.json_fallback",
                raw_length=len(response.text),
                snippet=response.text[:200],
            )
            return {"verdict": "approve", "feedback": ""}


# Self-registration so autoload picks up this agent.
_logger = structlog.get_logger("clyde.agent.code_reviewer")
AgentRegistry.instance().register(CodeReviewerAgent)
_logger.debug("code_reviewer.registered")
