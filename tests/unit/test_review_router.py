"""Tests for the ReviewRouter conditional edge function.

The router decides what happens after Code Reviewer gives its verdict:
approve -> QA Engineer, reject -> Senior Developer, exhausted -> needs_human.
"""

from __future__ import annotations

from src.config import constants
from src.engine.routers.review_router import ReviewRouter


class TestReviewRouter:
    async def test_approve_goes_to_qa(self):
        router = ReviewRouter(max_iterations=3)
        state = {"review_verdict": constants.CODE_REVIEW_APPROVE, "review_iteration": 1}

        assert router(state) == "qa_engineer"

    async def test_reject_goes_to_senior_dev(self):
        router = ReviewRouter(max_iterations=3)
        state = {"review_verdict": constants.CODE_REVIEW_REQUEST_CHANGES, "review_iteration": 1}

        assert router(state) == "senior_developer"

    async def test_exhausted_goes_to_needs_human(self):
        """Once the iteration count hits the max, it should escalate to a human."""
        router = ReviewRouter(max_iterations=3)
        state = {"review_verdict": constants.CODE_REVIEW_REQUEST_CHANGES, "review_iteration": 3}

        assert router(state) == "needs_human"

    async def test_approve_ignores_iteration_count(self):
        """Even at the iteration limit, an approval should still go to QA."""
        router = ReviewRouter(max_iterations=1)
        state = {"review_verdict": constants.CODE_REVIEW_APPROVE, "review_iteration": 5}

        assert router(state) == "qa_engineer"

    async def test_missing_verdict_treated_as_rejection(self):
        """If verdict is somehow absent, the router should not crash."""
        router = ReviewRouter(max_iterations=3)
        state = {"review_iteration": 1}

        # No verdict -> falls through the approve check, goes to reject path
        result = router(state)
        assert result in ("senior_developer", "needs_human")
