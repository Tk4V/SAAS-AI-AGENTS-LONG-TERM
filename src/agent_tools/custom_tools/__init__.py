"""In-process MCP servers exposing Clyde-specific skills to SDK agents.

External MCP servers (GitHub Copilot, Slack, Jira, AWS) are configured in
the ``mcp_server_configs`` table and built by ``AgentConfigService``. This
package is for skills that run inside the Python process — typically
because the upstream MCP does not expose the operation we need (e.g.
GitHub Copilot's MCP has no actions toolset, so we ship our own
``get_failed_ci_logs`` here).

Each provider package exports a ``build_*_skills_server(...)`` factory
that closes over per-session context (auth tokens, etc.) and returns a
``McpSdkServerConfig`` ready to drop into ``ClaudeAgentOptions.mcp_servers``.
Agents register the resulting in-process servers via the
``BaseAgent.build_in_process_mcp_servers`` hook.
"""

from src.agent_tools.custom_tools.github import build_github_skills_server

__all__ = ["build_github_skills_server"]
