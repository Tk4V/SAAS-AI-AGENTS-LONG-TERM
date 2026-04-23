"""Builds the LangGraph state graph.

Pipeline: developer → publisher → qa_engineer → END
"""

from typing import TYPE_CHECKING

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from src.common.exceptions import AppError
from src.engine.registry import AgentRegistry
from src.engine.state import TaskState

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


class PipelineBuildError(AppError):
    code = "pipeline_build_error"
    http_status = 500


class PipelineGraphBuilder:

    DEFAULT_DEV_PIPELINE: tuple[str, ...] = (
        "developer",
        "publisher",
        "qa_engineer",
    )

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def build_default(self, checkpointer: BaseCheckpointSaver) -> "CompiledStateGraph":
        self._ensure_registered(self.DEFAULT_DEV_PIPELINE)

        graph: StateGraph = StateGraph(TaskState)
        for name in self.DEFAULT_DEV_PIPELINE:
            graph.add_node(name, self._registry.get(name)())

        graph.add_edge(START, "developer")
        graph.add_edge("developer", "publisher")
        graph.add_edge("publisher", "qa_engineer")
        graph.add_edge("qa_engineer", END)

        return graph.compile(checkpointer=checkpointer)

    def build_from_config(self, config: dict, checkpointer: BaseCheckpointSaver) -> "CompiledStateGraph":
        raise NotImplementedError("Custom pipelines arrive in Milestone 2.")

    def _ensure_registered(self, names: tuple[str, ...]) -> None:
        missing = [n for n in names if n not in self._registry.all()]
        if missing:
            raise PipelineBuildError("Missing agents.", details={"missing": missing})
