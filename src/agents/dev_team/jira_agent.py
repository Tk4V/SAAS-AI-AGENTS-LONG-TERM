"""Jira agent — creates and manages Jira issues via the Atlassian MCP server.

Used when a task targets project management work (ticket creation, sprint
planning, etc.) rather than code changes.  No GitHub token or repository is
required; the only prerequisite is a connected Jira OAuth credential.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from src.agents.prompts.dev_team.jira_prompts import SYSTEM_PROMPT
from src.agents.sdk_agent import SDKAgent
from src.utils.exceptions import PipelineError


class JiraAgent(SDKAgent):
    """Autonomous Jira project-management agent powered by Claude Agent SDK.

    Resolves the user's Jira OAuth credential, spins up an MCP-authenticated
    SDK session scoped to a throwaway temp directory (no repo needed), and
    executes the requested ticket-management task.
    """

    name: ClassVar[str] = "jira"
    role: ClassVar[str] = "Jira Project Manager"

    SDK_ALLOWED_TOOLS: ClassVar[list[str]] = ["mcp__jira__*"]

    # Jira tasks are pure project-management — Sonnet is plenty capable and
    # cheaper than Opus for this kind of structured tool-calling work.
    SDK_MODEL: ClassVar[str] = "claude-sonnet-4-6"
    SDK_MAX_TURNS: ClassVar[int] = 60

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run a Jira MCP session and return the result summary."""
        user_id = state.get("user_id")
        description = state.get("description") or ""

        if not user_id:
            raise PipelineError("Jira agent requires a user_id in the pipeline state.")

        jira_context = await self.resolve_jira_token(user_id=user_id)
        if not jira_context:
            raise PipelineError(
                "Jira agent requires a connected Jira account. "
                "Please connect Jira via Settings → Integrations before running this task."
            )

        jira_token, jira_site_url, jira_cloud_id = jira_context

        self.logger.info(
            "jira.session_starting",
            task_description=description[:100],
            site_url=jira_site_url,
        )

        with tempfile.TemporaryDirectory(prefix="clyde_jira_") as tmp:
            session_summary = await self.run_sdk_session(
                prompt=f"{SYSTEM_PROMPT}\n\n---\n\nTask:\n{description}",
                working_directory=Path(tmp),
                mcp_context={
                    "jira_token": jira_token,
                    "jira_site_url": jira_site_url,
                    "jira_cloud_id": jira_cloud_id,
                },
            )

        self.logger.info("jira.session_completed", summary_length=len(session_summary))

        return {
            "context": {"summary": session_summary[:2000]},
            "events": [{
                "name": "jira.completed",
                "agent": self.name,
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": {"summary": session_summary[:500]},
            }],
        }

    async def build_mcp_servers(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return the Jira MCP server config authenticated with the user's token."""
        from src.agent_tools.mcp import jira_mcp_server

        jira_token = context.get("jira_token")
        jira_site_url = context.get("jira_site_url")
        jira_cloud_id = context.get("jira_cloud_id")

        if not jira_token or not jira_site_url or not jira_cloud_id:
            raise PipelineError(
                "JiraAgent.build_mcp_servers requires 'jira_token', "
                "'jira_site_url', and 'jira_cloud_id' in mcp_context."
            )

        return {"jira": jira_mcp_server(jira_token, jira_site_url, jira_cloud_id)}
