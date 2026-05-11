"""Orchestrator agent — generalist Claude Agent SDK session leader.

Receives a free-form user task, optionally clones the user's repositories
when code access is needed, and runs a Claude Agent SDK session that
delegates work to specialised sub-agents (code-implementer, code-explorer,
test-runner, manager, code-auditor). Returns file diffs and a summary.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

from claude_agent_sdk import AgentDefinition

from src.agent_tools.custom_tools.memory.graph_writer import GraphWriter
from src.agent_tools.custom_tools.memory.mcp_server import create_memory_mcp_server
from src.agent_tools.custom_tools.github import (
    CLYDE_GITHUB_SERVER_NAME,
    build_github_skills_server,
)
from src.agents.prompts.team.orchestrator_prompts import (
    BASE_SYSTEM_PROMPT as _ORCHESTRATOR_BASE_PROMPT,
    build_system_prompt as _build_orchestrator_prompt,
)
from src.agents.sdk_agent import SDKAgent
from src.db.queries.agent_config_query import AgentConfigRepository, TeamAgentConfigRepository
from src.db.queries.agent_query import AgentRepository
from src.db.session import db
from src.integrations.github import GitHubGitOps
from src.utils.exceptions import PipelineError


class OrchestratorAgent(SDKAgent):
    """Generalist orchestrator that classifies a task and delegates to sub-agents.

    Handles only the first repository in the task state when code access is
    needed. Multi-repo support will require running separate SDK sessions
    per repo.
    """

    name: ClassVar[str] = "orchestrator"
    role: ClassVar[str] = "Orchestrator"
    # Filled per-instance from the user's Agent record (or the base prompt
    # if the user has no override). Set in execute() before run_sdk_session.
    SDK_SYSTEM_PROMPT: ClassVar[str | None] = _ORCHESTRATOR_BASE_PROMPT

    SDK_ALLOWED_TOOLS: ClassVar[list[str]] = []
    SYSTEM_TOOLS: ClassVar[list[str]] = [
        "Read", "Edit", "Write", "Glob", "Grep",
        "Bash(git diff*)", "Bash(python -m py_compile*)", "Agent",
        "mcp__memory__*",
    ]

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Bootstrap a persistent chat session for this task.

        The execution model changed in CA-113: instead of running a single
        SDK query loop and returning diffs to a separate Publisher step,
        we keep a long-lived ``SDKChatSession`` open. After every agent
        turn a post-turn callback (the auto-publisher) commits and pushes
        whatever changed. The session ends only when the user explicitly
        closes it or one of the timeouts fires.

        Returns a small state patch — the bulk of per-turn state now
        lives in ``task_messages`` and the task's own status transitions.
        """
        task_id_raw = state.get("task_id") or "unknown"
        user_id = state.get("user_id")
        agent_id_raw = state.get("agent_id")
        description = state.get("description") or ""
        repositories = state.get("repos") or []

        if not user_id:
            raise PipelineError("Orchestrator agent requires a user_id in the pipeline state.")
        if not agent_id_raw:
            raise PipelineError("Orchestrator agent requires an agent_id in the pipeline state.")
        agent_id = UUID(agent_id_raw) if isinstance(agent_id_raw, str) else agent_id_raw
        task_id_uuid = UUID(task_id_raw) if isinstance(task_id_raw, str) else task_id_raw

        # Resolve the user's Agent record + dynamically build the system prompt.
        async with db.session_scope() as session:
            agent_repo = AgentRepository(session)
            agent_record = await agent_repo.get(user_id=user_id, agent_id=agent_id)
            links = await agent_repo.list_subagents_for_agent(agent_id=agent_id)
            team_cfg_repo = TeamAgentConfigRepository(session)
            team_cfg = await team_cfg_repo.get("orchestrator")
            session.expunge_all()

        subagent_descriptors = [
            (link.subagent.name, link.subagent.display_name, link.subagent.description)
            for link in links
        ]
        if team_cfg is not None:
            self.SDK_MODEL = team_cfg.model  # type: ignore[assignment]
            if team_cfg.system_tools:
                self.SYSTEM_TOOLS = [  # type: ignore[assignment]
                    st.system_tool.pattern
                    for st in team_cfg.system_tools
                    if st.is_active
                ]
        self.SDK_SYSTEM_PROMPT = _build_orchestrator_prompt(
            agent_record.system_prompt,
            subagent_descriptors,
            base_prompt=team_cfg.system_prompt if team_cfg else None,
        )
        self._agent_id = agent_id
        self._cached_links = links

        # ── memory graph ──────────────────────────────────────────────────────
        graph_writer = GraphWriter()
        task_node_id = await graph_writer.create_task_node(
            task_id=task_id_raw,
            user_id=user_id,
            agent_id=agent_id,
            description=description,
            attempt=state.get("attempt", 0),
        )

        workspace_path = Path(tempfile.mkdtemp(prefix=f"clyde_{task_id_raw}_"))
        cloned_repos: list[dict[str, Any]] = []
        primary_repo_path = workspace_path
        primary_repo_name: str | None = None

        try:
            if repositories:
                github_token = await self.resolve_github_token(user_id=user_id)
                cloned_repos = await self._clone_all_repositories(
                    github_token=github_token,
                    repositories=repositories,
                    workspace_path=workspace_path,
                )
                primary_repo_path = Path(cloned_repos[0]["local_path"])
                primary_repo_name = cloned_repos[0]["name"]

            self.logger.info(
                "orchestrator.chat_session_starting",
                repository=primary_repo_name,
                has_repo=primary_repo_name is not None,
                task_description=description[:100],
            )

            # Persist workspace + repo metadata to task.state so the
            # post-turn callback (which loads the task fresh on every
            # turn) can find the cloned tree.
            from src.db.queries.task_query import TaskRepository
            async with db.session_scope() as session:
                task_repo = TaskRepository(session)
                task = await task_repo.get(user_id=user_id, task_id=task_id_uuid)
                await task_repo.update_status(
                    task=task, status=task.status,
                    state_patch={
                        "workspace_path": str(workspace_path),
                        "repos": cloned_repos,
                        "primary_repo_name": primary_repo_name,
                    },
                )

            # The post-turn callback is wired up by ``task_service`` after
            # this method returns the session config — it has visibility
            # into the publisher, while this agent does not.
            chat_session = await self.build_chat_session(
                initial_prompt=description,
                working_directory=primary_repo_path,
                task_id=task_id_uuid,
                user_id=user_id,
                mcp_context={
                    "user_id": user_id,
                    "task_node_id": task_node_id,
                },
                post_turn_callback=state.get("_post_turn_callback"),
            )

            # Register with the process-wide service so the WS handler
            # can call request_close(task_id) and the lifespan shutdown
            # can drain it. Awaits the underlying run() to keep this
            # call site blocking — the orchestrator coroutine ends only
            # when the chat session ends.
            from src.services.chat_session_service import chat_session_service
            session_task = await chat_session_service.register_and_run(chat_session)
            await session_task
            self.logger.info(
                "orchestrator.chat_session_finished",
                turns=chat_session.turn_count,
            )
            await graph_writer.finish_task(task_node_id=task_node_id, status="completed")

            return {
                "repos": cloned_repos,
                "primary_repo_name": primary_repo_name,
                "workspace_path": str(workspace_path),
                "events": [{
                    "name": "orchestrator.completed",
                    "agent": self.name,
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "payload": {"turns": chat_session.turn_count},
                }],
            }
        except Exception:
            await graph_writer.finish_task(task_node_id=task_node_id, status="failed")
            raise
        finally:
            # Workspace cleanup happens here regardless of success — the
            # publisher (in the post-turn callback) is responsible for
            # pushing changes during the session; nothing in the workspace
            # is needed once the session ends.
            shutil.rmtree(workspace_path, ignore_errors=True)

    async def build_mcp_servers(self, context: dict[str, Any]) -> dict[str, Any]:
        """Mount MCP servers for all integrations the user has connected."""
        user_id: int = context["user_id"]
        task_node_id: int | None = context.get("task_node_id")
        servers = await self.build_user_mcp_servers(user_id=user_id)
        servers["memory"] = create_memory_mcp_server(
            user_id=user_id,
            task_node_id=task_node_id,
        )
        return servers

    async def build_in_process_mcp_servers(
        self,
        user_id: int | None,
        task_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Add Clyde's in-process skill servers.

        Always inherits ``clyde_chat`` (with ``ask_user``) from the base
        agent. Additionally registers ``clyde_github`` when the user has
        a GitHub OAuth credential — the skill plugs gaps the upstream
        Copilot MCP does not cover (e.g. Actions log retrieval) and needs
        the same token.
        """
        servers = await super().build_in_process_mcp_servers(
            user_id=user_id, task_id=task_id
        )
        if user_id is None:
            return servers
        try:
            github_token = await self.resolve_github_token(user_id=user_id)
        except Exception:
            return servers
        servers[CLYDE_GITHUB_SERVER_NAME] = build_github_skills_server(
            github_token=github_token,
        )
        return servers

    async def build_subagents(self, context: dict[str, Any]) -> dict[str, Any]:
        """Specialised sub-agents the orchestrator delegates to.

        Driven entirely from the database: subagent config (description,
        system_prompt, model) comes from ``subagents``, system tools come
        from ``subagent_system_tools`` (admin-defined), and MCP tools come
        from ``agent_subagent_mcps`` (per-link, user-controlled). Only the
        subagents the user attached to *this* orchestrator are returned.
        """
        user_id: int | None = context.get("user_id")
        links = getattr(self, "_cached_links", None)
        if links is None:
            raise PipelineError(
                "build_subagents called before execute() loaded the agent.",
            )

        async with db.session_scope() as session:
            cfg_repo = AgentConfigRepository(session)
            connected = (
                await cfg_repo._get_connected_providers(user_id) if user_id else set()
            )

        # In-process skill servers (clyde_github, …) do not have OAuth
        # credentials, so they would not pass the credential-backed
        # filter below. Add their provider names explicitly so subagents
        # that declare a SubagentTool link to them still see the tool.
        # `clyde_chat` (the ``ask_user`` tool) is intentionally excluded —
        # only the orchestrator talks to the user; subagents that need
        # missing context return a structured request to the orchestrator
        # which then asks on their behalf. Single voice to the user.
        from src.agent_tools.custom_tools import CLYDE_CHAT_SERVER_NAME
        task_id: UUID | None = context.get("task_id")
        in_process_servers = await self.build_in_process_mcp_servers(
            user_id=user_id, task_id=task_id
        )
        connected = connected | (
            set(in_process_servers.keys()) - {CLYDE_CHAT_SERVER_NAME}
        )

        result: dict[str, Any] = {}
        for link in links:
            subagent = link.subagent
            system_tool_patterns = [
                st.system_tool.pattern
                for st in subagent.system_tools
                if st.is_active and st.system_tool.is_active
            ]
            active_mcp_providers = [
                m.mcp_server.provider_name
                for m in link.mcps
                if m.is_active and m.mcp_server.provider_name in connected
            ]
            mcp_patterns = [f"mcp__{p}__*" for p in active_mcp_providers]
            result[subagent.name] = AgentDefinition(
                description=subagent.description,
                prompt=subagent.system_prompt,
                tools=system_tool_patterns + mcp_patterns,
                model=subagent.model,
                mcpServers=active_mcp_providers or None,
            )
        return result

    async def _clone_all_repositories(
        self,
        *,
        github_token: str,
        repositories: list[dict[str, Any]],
        workspace_path: Path,
    ) -> list[dict[str, Any]]:
        """Clone all task repositories concurrently into the workspace directory."""

        async def clone_one(repository: dict[str, Any]) -> dict[str, Any]:
            url = repository.get("url")
            if not url:
                raise PipelineError("Repository entry is missing the 'url' field.")

            coordinates = GitHubGitOps.parse_repo_url(url)
            branch = repository.get("default_branch") or "main"
            cloned = await GitHubGitOps.clone(
                coordinates=coordinates,
                token=github_token,
                branch=branch,
                destination=workspace_path / coordinates.name,
            )

            return {
                "name": coordinates.name,
                "url": url,
                "default_branch": branch,
                "local_path": str(cloned.local_path),
                "branch": cloned.branch,
                "head_commit": cloned.head_commit,
            }

        return await asyncio.gather(*[clone_one(repo) for repo in repositories])

    @staticmethod
    async def _collect_file_changes(
        *,
        repository_path: Path,
        repository_name: str,
    ) -> dict[str, list[dict[str, str]]]:
        """Collect tracked modifications and untracked new files after the SDK session."""
        tracked_output = await OrchestratorAgent._run_git_command(
            "git", "diff", "--name-status", "HEAD",
            working_directory=repository_path,
        )
        untracked_output = await OrchestratorAgent._run_git_command(
            "git", "ls-files", "--others", "--exclude-standard",
            working_directory=repository_path,
        )

        changes: list[dict[str, str]] = []

        for line in tracked_output.strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                status_code, file_path = parts
                action = "create" if status_code.startswith("A") else "modify"
                changes.append({"path": file_path, "action": action})

        for line in untracked_output.strip().splitlines():
            file_path = line.strip()
            if file_path:
                changes.append({"path": file_path, "action": "create"})

        return {repository_name: changes} if changes else {}

    @staticmethod
    async def _run_git_command(*args: str, working_directory: Path) -> str:
        """Execute a git command asynchronously and return stdout."""
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(working_directory),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        return stdout.decode()
