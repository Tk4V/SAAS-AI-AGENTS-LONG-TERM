"""Tests for the TestRouter conditional edge function.

Routes after QA Engineer: pass -> Release Manager, fail -> Senior Developer,
exhausted -> needs_human.
"""

from __future__ import annotations

from src.config import constants
from src.engine.routers.test_router import TestRouter


class TestTestRouter:
    async def test_pass_goes_to_release_manager(self):
        router = TestRouter(max_iterations=3)
        state = {"qa_verdict": constants.QA_RESULT_PASS, "qa_iteration": 1}

        assert router(state) == "release_manager"

    async def test_fail_goes_to_senior_dev(self):
        router = TestRouter(max_iterations=3)
        state = {"qa_verdict": constants.QA_RESULT_FAIL, "qa_iteration": 1}

        assert router(state) == "senior_developer"

    async def test_exhausted_goes_to_needs_human(self):
        """Once QA iterations are maxed out, escalate to a human."""
        router = TestRouter(max_iterations=3)
        state = {"qa_verdict": constants.QA_RESULT_FAIL, "qa_iteration": 3}

        assert router(state) == "needs_human"

    async def test_pass_ignores_iteration_count(self):
        """A pass verdict always moves forward regardless of iteration count."""
        router = TestRouter(max_iterations=1)
        state = {"qa_verdict": constants.QA_RESULT_PASS, "qa_iteration": 10}

        assert router(state) == "release_manager"

    async def test_missing_verdict_treated_as_failure(self):
        """When qa_verdict is absent, the router should not crash."""
        router = TestRouter(max_iterations=3)
        state = {"qa_iteration": 1}

        result = router(state)
        assert result in ("senior_developer", "needs_human")
