"""Routes the pipeline after Code Reviewer has produced a verdict.

If the reviewer approves, the work moves on to QA. If the reviewer asks for
changes, control goes back to Senior Developer — but no more than
`max_iterations` times. Once the limit is hit we proceed to QA anyway so
the pipeline always produces a PR.
"""

from src.config import constants, get_settings
from src.engine.state import TaskState


class ReviewRouter:
    """Conditional edge function for the Code Reviewer node."""

    APPROVE_NEXT = "qa_engineer"
    REJECT_NEXT = "senior_developer"
    EXHAUSTED_NEXT = "qa_engineer"

    def __init__(self, max_iterations: int | None = None) -> None:
        self._max_iterations = (
            max_iterations
            if max_iterations is not None
            else get_settings().max_review_iterations
        )

    def __call__(self, state: TaskState) -> str:
        verdict = state.get("review_verdict")
        if verdict == constants.CODE_REVIEW_APPROVE:
            return self.APPROVE_NEXT

        iteration = int(state.get("review_iteration") or 0)
        if iteration >= self._max_iterations:
            return self.EXHAUSTED_NEXT
        return self.REJECT_NEXT
