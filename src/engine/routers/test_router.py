"""Routes the pipeline after QA Engineer has produced a verdict.

PASS goes to Release Manager. FAIL bounces back to Senior Developer to fix
the failing tests, capped by `max_iterations` to avoid infinite loops.
Once the limit is hit we proceed to Release Manager anyway so the pipeline
always produces a PR.
"""

from src.config import constants, get_settings
from src.engine.state import TaskState


class TestRouter:
    """Conditional edge function for the QA Engineer node."""

    PASS_NEXT = "release_manager"
    FAIL_NEXT = "senior_developer"
    EXHAUSTED_NEXT = "release_manager"

    def __init__(self, max_iterations: int | None = None) -> None:
        self._max_iterations = (
            max_iterations
            if max_iterations is not None
            else get_settings().max_qa_iterations
        )

    def __call__(self, state: TaskState) -> str:
        verdict = state.get("qa_verdict")
        if verdict == constants.QA_RESULT_PASS:
            return self.PASS_NEXT

        iteration = int(state.get("qa_iteration") or 0)
        if iteration >= self._max_iterations:
            return self.EXHAUSTED_NEXT
        return self.FAIL_NEXT
