"""Builds the LangGraph state graph from declared pipeline shapes.

For Milestone 1 the only supported shape is the hard-coded development team
pipeline. The shape from M2 onwards will be loaded from the `pipelines` table
and validated before being compiled.
"""

from typing import TYPE_CHECKING

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from src.common.exceptions import AppError
from src.engine.registry import AgentRegistry
from src.engine.routers import ReviewRouter, TestRouter
from src.engine.state import TaskState

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


class PipelineBuildError(AppError):
    """Raised when a pipeline cannot be assembled (missing agent, bad config)."""

    code = "pipeline_build_error"
    http_status = 500


class PipelineGraphBuilder:
    """Compiles agent registries into ready-to-run LangGraph state graphs."""

    DEFAULT_DEV_PIPELINE: tuple[str, ...] = (
        "tech_lead",
        "architect",
        "senior_developer",
        "code_reviewer",
        "qa_engineer",
        "release_manager",
    )

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        review_router: ReviewRouter | None = None,
        test_router: TestRouter | None = None,
    ) -> None:
        self._registry = registry
        self._review_router = review_router or ReviewRouter()
        self._test_router = test_router or TestRouter()

    def build_default(
        self,
        checkpointer: BaseCheckpointSaver,
    ) -> "CompiledStateGraph":
        """Compile the M1 development team pipeline.

        Pipeline shape:
            START -> tech_lead -> architect -> senior_developer -> code_reviewer
            code_reviewer -- approve --> qa_engineer
                          -- changes --> senior_developer (up to N times)
                          -- exhausted --> END (status set to needs_human by service)
            qa_engineer -- pass --> release_manager -> END
                       -- fail --> senior_developer (up to N times)
                       -- exhausted --> END
        """
        self._ensure_registered(self.DEFAULT_DEV_PIPELINE)

        graph: StateGraph = StateGraph(TaskState)
        for name in self.DEFAULT_DEV_PIPELINE:
            agent_class = self._registry.get(name)
            graph.add_node(name, agent_class())

        graph.add_edge(START, "tech_lead")
        graph.add_edge("tech_lead", "architect")
        graph.add_edge("architect", "senior_developer")
        graph.add_edge("senior_developer", "code_reviewer")

        graph.add_conditional_edges(
            "code_reviewer",
            self._review_router,
            {
                ReviewRouter.APPROVE_NEXT: "qa_engineer",
                ReviewRouter.REJECT_NEXT: "senior_developer",
                ReviewRouter.EXHAUSTED_NEXT: "qa_engineer",
            },
        )
        graph.add_conditional_edges(
            "qa_engineer",
            self._test_router,
            {
                TestRouter.PASS_NEXT: "release_manager",
                TestRouter.FAIL_NEXT: "senior_developer",
                TestRouter.EXHAUSTED_NEXT: "release_manager",
            },
        )
        graph.add_edge("release_manager", END)

        return graph.compile(checkpointer=checkpointer)

    def build_from_config(
        self,
        config: dict,
        checkpointer: BaseCheckpointSaver,
    ) -> "CompiledStateGraph":
        """Reserved for user-defined pipelines in Milestone 2."""
        raise NotImplementedError(
            "Custom pipelines arrive in Milestone 2. Use build_default for now.",
        )

    def _ensure_registered(self, names: tuple[str, ...]) -> None:
        missing = [name for name in names if name not in self._registry.all()]
        if missing:
            raise PipelineBuildError(
                "Pipeline references agents that have not been registered.",
                details={"missing": missing},
            )
