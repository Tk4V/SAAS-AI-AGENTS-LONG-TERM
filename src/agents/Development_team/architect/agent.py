"""Architect agent — designs a cross-repo change plan from the Tech Lead's context.

Receives the task description, the Tech Lead's context analysis, and the list
of repositories. Produces a structured plan that tells the Senior Developer
exactly which files to create, modify, or delete in each repo and why.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from src.agents.base import BaseAgent
from src.agents.development_team.architect.prompts import (
    PLAN_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
)
from src.common.exceptions import PipelineError
from src.engine.registry import AgentRegistry
from src.tools import toolbox
from src.tools.llm.gateway import ChatMessage, LLMGateway

if TYPE_CHECKING:
    from src.engine.state import TaskState


class ArchitectAgent(BaseAgent):
    name = "architect"
    role = "Architect"

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
        repos = state.get("repos") or []

        if not description:
            raise PipelineError("Architect invoked without a task description.")
        if not context:
            raise PipelineError("Architect invoked without Tech Lead context.")

        plan = await self._generate_plan(
            description=description,
            context=context,
            repos=repos,
        )

        event = {
            "name": "architect.plan_created",
            "agent": self.name,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": {
                "repo_count": len(plan.get("repos", [])),
                "total_changes": sum(
                    len(r.get("changes", [])) for r in plan.get("repos", [])
                ),
            },
        }

        return {
            "plan": plan,
            "events": [event],
        }

    async def _generate_plan(
        self,
        *,
        description: str,
        context: dict[str, Any],
        repos: list[dict[str, Any]],
    ) -> dict[str, Any]:
        user_message = PLAN_PROMPT_TEMPLATE.format(
            description=description,
            context=json.dumps(context, indent=2),
            repos=json.dumps(repos, indent=2),
        )

        response = await self._llm.chat(
            role="architect",
            system=SYSTEM_PROMPT,
            messages=[ChatMessage(role="user", content=user_message)],
        )

        self.logger.info(
            "architect.llm_response",
            model=response.model,
            tokens=response.usage.total,
        )

        from src.common.json_utils import parse_llm_json
        return parse_llm_json(response.text, agent="Architect")


# Self-registration so autoload picks up this agent.
_logger = structlog.get_logger("clyde.agent.architect")
AgentRegistry.instance().register(ArchitectAgent)
_logger.debug("architect.registered")
