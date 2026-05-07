"""Skill-tool classes for the GitHub in-process MCP server.

Each class subclasses ``BaseSkillTool``, declares ``name``,
``description``, ``input_schema`` as class-level fields, captures any
per-session credentials in ``__init__``, and implements ``run``. The
server in ``server.py`` instantiates them and hands the list to
``build_mcp_server``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from src.agent_tools.custom_tools.github.helpers import fetch_failed_run_logs
from src.agent_tools.custom_tools.mcp_server_builder import BaseSkillTool


class GetFailedCILogsTool(BaseSkillTool):
    """Returns the tail of every failed job's log for a workflow run.

    Used by the ``devops`` sub-agent as the first step when diagnosing
    a CI failure — grounds reasoning in the actual error text before any
    file inspection.
    """

    name: ClassVar[str] = "get_failed_ci_logs"
    description: ClassVar[str] = (
        "Fetch the last lines of every failed job's log for a GitHub "
        "Actions workflow run. Returns a multi-section plain-text "
        "string with one section per failed job. Use this as the "
        "first step when diagnosing a CI failure: read the actual "
        "error text before inspecting the source code."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "run_id": int,
        "repo_full_name": str,
    }

    def __init__(self, github_token: str) -> None:
        self._token = github_token

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        text = await fetch_failed_run_logs(
            token=self._token,
            repo_full_name=args["repo_full_name"],
            run_id=args["run_id"],
        )
        return {"content": [{"type": "text", "text": text}]}
