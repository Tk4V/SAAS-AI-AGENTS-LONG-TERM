"""QA Engineer agent — runs tests in sandboxed containers and analyses results.

For each repository with changes, runs pytest inside a Docker sandbox via
SandboxRunner. If all repos pass, the verdict is "pass". If any fail, the
agent uses an LLM call to analyse the failure output and the verdict is "fail".
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from anthropic import AsyncAnthropic

from src.agents.base_agent import BaseAgent
from src.agents.prompts.dev_team.qa_prompts import (
    FAILURE_ANALYSIS_TEMPLATE,
    SYSTEM_PROMPT,
)
from src.config import get_settings
from src.config.constants import QA_RESULT_FAIL, QA_RESULT_PASS
from src.tools import toolbox
from src.tools.custom_tools.sandbox.sandbox_runner import SandboxRunner

# Default Docker image for Python repos.
_PYTHON_IMAGE = "python:3.12-slim"
_PYTEST_COMMAND = ("pytest", "--tb=short", "-q")
_TEST_TIMEOUT_SEC = 300


class QAEngineerAgent(BaseAgent):
    """Runs automated tests in sandboxed containers and reports pass/fail verdicts.

    For each repository that has diffs the agent spins up a Docker sandbox,
    executes pytest, and collects the results. Failed runs are sent to the LLM
    for analysis so the Developer agent gets actionable feedback on the next
    pipeline iteration.
    """

    name: ClassVar[str] = "qa_engineer"
    role: ClassVar[str] = "QA Engineer"

    def __init__(
        self,
        *,
        sandbox: SandboxRunner | None = None,
    ) -> None:
        super().__init__()
        self._sandbox = sandbox or toolbox.sandbox

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run tests for every changed repo and return the aggregated verdict."""
        repos = state.get("repos") or []
        diffs = state.get("diffs") or {}
        iteration = int(state.get("qa_iteration") or 0)

        if not repos:
            raise ValueError("QA Engineer invoked without repositories.")

        # Only test repos that have changes.
        repos_to_test = [r for r in repos if r.get("name") in diffs]
        if not repos_to_test:
            repos_to_test = repos

        qa_results: dict[str, Any] = {}
        all_passed = True

        for repo in repos_to_test:
            repo_name = repo.get("name", "unknown")
            local_path = repo.get("local_path", "")
            if not local_path:
                self.logger.warning("qa_engineer.missing_local_path", repo=repo_name)
                continue

            try:
                result = await self._sandbox.run(
                    repo_path=Path(local_path),
                    command=list(_PYTEST_COMMAND),
                    image=_PYTHON_IMAGE,
                    timeout_sec=_TEST_TIMEOUT_SEC,
                )
            except Exception as exc:
                self.logger.warning(
                    "qa_engineer.sandbox_failed",
                    repo=repo_name,
                    error=str(exc),
                )
                qa_results[repo_name] = {
                    "repo": repo_name,
                    "passed": True,
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": f"Sandbox unavailable: {exc}. Skipping tests.",
                    "duration_sec": 0,
                    "timed_out": False,
                }
                continue

            outcome = result.to_dict()
            outcome["repo"] = repo_name
            qa_results[repo_name] = outcome

            if not result.passed:
                all_passed = False
                self.logger.info(
                    "qa_engineer.tests_failed",
                    repo=repo_name,
                    exit_code=result.exit_code,
                )
                # LLM analysis gives the Developer useful feedback when the
                # pipeline loops back for another attempt.
                analysis = await self._analyse_failure(
                    repo_name=repo_name, result=result
                )
                outcome["analysis"] = analysis

        verdict = QA_RESULT_PASS if all_passed else QA_RESULT_FAIL
        new_iteration = iteration + 1

        event = {
            "name": "qa_engineer.tests_completed",
            "agent": self.name,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": {
                "verdict": verdict,
                "repos_tested": len(qa_results),
            },
        }

        return {
            "qa_results": qa_results,
            "qa_verdict": verdict,
            "qa_iteration": new_iteration,
            "events": [event],
        }

    async def _analyse_failure(
        self,
        *,
        repo_name: str,
        result: Any,
    ) -> dict[str, Any]:
        """Ask the LLM to summarise why a test run failed.

        Truncates stdout/stderr to avoid blowing token limits, then parses
        the LLM response as JSON. Falls back to a plain-text summary dict
        if parsing fails.
        """
        stdout_tail = (
            result.stdout[-4000:]
            if len(result.stdout) > 4000
            else result.stdout
        )
        stderr_tail = (
            result.stderr[-2000:]
            if len(result.stderr) > 2000
            else result.stderr
        )
        user_message = FAILURE_ANALYSIS_TEMPLATE.format(
            repo_name=repo_name,
            stdout=stdout_tail,
            stderr=stderr_tail,
            exit_code=result.exit_code,
            duration_sec=result.duration_sec,
        )

        settings = get_settings()
        client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
        response = await client.messages.create(
            model=settings.anthropic_model_sonnet,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text

        self.logger.info(
            "qa_engineer.analysis_complete",
            repo=repo_name,
            model=response.model,
        )

        from src.utils.json_parser import LLMJsonParser

        try:
            return LLMJsonParser.parse(text, agent="QA Engineer")
        except Exception:
            return {"summary": text, "failed_tests": [], "suggestion": ""}
