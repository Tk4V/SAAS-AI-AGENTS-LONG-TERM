from __future__ import annotations

from fastapi import APIRouter

from src.api.schemas.agent_schemas import AgentRead, AgentsList

router = APIRouter(prefix="/agents", tags=["agents"])

_AGENTS = AgentsList(
    items=[
        AgentRead(
            id="orchestrator",
            name="Orchestrator",
            description="Generalist agent that classifies a task and delegates to sub-agents.",
        ),
        AgentRead(
            id="publisher",
            name="Publisher",
            description="Commits changes, pushes branches, and creates pull requests via GitHub.",
        ),
    ]
)


class AgentsView:
    @staticmethod
    @router.get("", response_model=AgentsList)
    async def list() -> AgentsList:
        return _AGENTS
