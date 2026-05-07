"""Base class for agents that drive an autonomous Claude Agent SDK session.

Extends ``BaseAgent`` with the SDK configuration contract: every concrete
SDK agent must declare its allowed tool list and implement how to assemble
the MCP-server map for its sessions. The shared ``run_sdk_session`` helper
handles the streaming/logging boilerplate so subclasses focus on what's
actually agent-specific.
"""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from src.agents.base_agent import BaseAgent
from src.db.queries.agent_config_query import AgentConfigRepository
from src.db.session import db


class SDKAgent(BaseAgent):
    """Abstract base for agents that launch a Claude Agent SDK session.

    To create a new SDK agent:
        1. Fulfil the ``BaseAgent`` contract (``name``, ``role``, ``execute``).
        2. Declare ``SDK_ALLOWED_TOOLS`` — the list of tool name patterns the
           LLM is allowed to call inside the session (e.g. ``["Read", "Edit",
           "Bash(git diff*)", "mcp__github__*"]``). No default — every
           subclass must decide this explicitly. Enforced at class-definition
           time via ``__init_subclass__``. Include ``"Agent"`` if the agent
           also returns subagents from ``build_subagents``.
        3. Implement ``async build_mcp_servers(self, context) -> dict``. Return
           the ``mcp_servers`` map handed to ``ClaudeAgentOptions`` — typically
           ``{"github": github_mcp_server(token)}`` for external stdio servers
           or ``{"clyde_git": clyde_git_mcp_server}`` for in-process custom
           ones. Return ``{}`` if the agent uses only built-in SDK tools.
        4. Optionally override ``build_subagents(self, context) -> dict`` to
           expose specialised subagents Claude may spawn during the session
           (code-reviewer, test-runner, etc.). Default returns ``{}`` (no
           subagents).
        5. Optionally override ``SDK_MODEL`` / ``SDK_MAX_TURNS`` /
           ``SDK_PERMISSION_MODE`` if the defaults don't suit.

    Subclasses then call ``await self.run_sdk_session(...)`` from inside
    ``execute`` — that method takes care of streaming SDK messages, logging
    every assistant turn and tool call/result, and returning the final
    result text.
    """

    # Built-in SDK tools always available for this agent — never in DB,
    # never user-configurable. Subclasses declare the exact set they need
    # (Read, Edit, Write, Glob, Grep, Agent, Bash variants, etc.).
    # Integration tools (mcp__*) are resolved at runtime from the DB and
    # filtered by the user's connected credentials.
    SYSTEM_TOOLS: ClassVar[list[str]] = []

    # No default — concrete subclass MUST set this. __init_subclass__ enforces.
    SDK_ALLOWED_TOOLS: ClassVar[list[str]]

    # Parent runs on Opus — its job is to plan, read selectively, and delegate
    # heavy lifting to sub-agents. Sub-agents (defined in `build_subagents`)
    # handle the actual implementation on Sonnet/Haiku, so Opus turns stay
    # focused on orchestration and don't get burned on `Read`/`Grep` chains.
    SDK_MODEL: ClassVar[str] = "claude-opus-4-7"
    SDK_MAX_TURNS: ClassVar[int] = 200
    SDK_PERMISSION_MODE: ClassVar[str] = "acceptEdits"
    SDK_SYSTEM_PROMPT: ClassVar[str | None] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Skip the check on still-abstract subclasses; only enforce on
        # concrete agent classes that can actually be instantiated.
        if getattr(cls, "__abstractmethods__", None):
            return
        tools = getattr(cls, "SDK_ALLOWED_TOOLS", None)
        if not isinstance(tools, list):
            raise TypeError(
                f"{cls.__name__} must declare SDK_ALLOWED_TOOLS as a list "
                f"(got {type(tools).__name__}). Use [] to load from DB. "
                f"See SDKAgent docstring.",
            )

    @abstractmethod
    async def build_mcp_servers(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return the MCP server map for this agent's SDK sessions.

        ``context`` is the free-form dict the caller passes to
        ``run_sdk_session(mcp_context=...)`` — use it to receive per-task
        data the hook needs (auth tokens, project ids, etc.) without
        smuggling it through instance state.

        Return ``{}`` when the agent needs no MCP servers — its
        ``SDK_ALLOWED_TOOLS`` then comprises only built-in SDK tools.
        """

    async def build_subagents(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return subagent definitions Claude may spawn during the session.

        Subagents run in isolated contexts: each starts with a fresh
        conversation, can have its own system prompt, restricted tool set,
        and (optionally) a different model. The parent only sees a
        subagent's final message — intermediate tool calls stay inside.

        Default returns ``{}`` — no subagents available. Override to register
        specialised subagents like ``code-reviewer`` or ``test-runner``::

            from claude_agent_sdk import AgentDefinition

            async def build_subagents(self, context):
                return {
                    "code-reviewer": AgentDefinition(
                        description="Expert code reviewer.",
                        prompt="You are a senior reviewer. Be thorough.",
                        tools=["Read", "Glob", "Grep"],
                        model="sonnet",
                    ),
                }

        ``Agent`` must be in ``SDK_ALLOWED_TOOLS`` for subagent invocation
        to work — ``run_sdk_session`` enforces this when this hook returns
        a non-empty map.

        ``context`` is the same free-form dict passed to ``build_mcp_servers``.
        Subagents may also be built dynamically from per-task data.
        """
        return {}

    async def _load_allowed_tools(self, *, user_id: int | None = None) -> list[str]:
        """Return the full allowed-tool list for a session.

        Combines two sources:
        - ``SYSTEM_TOOLS`` (ClassVar) — built-in SDK tools hardcoded per agent,
          never user-configurable.
        - MCP patterns from ``agent_tool_configs`` in the DB, filtered by the
          user's active credentials via ``get_effective_tool_patterns``.
        """
        async with db.session_scope() as session:
            repo = AgentConfigRepository(session)
            mcp_patterns = await repo.get_effective_tool_patterns(
                user_id=user_id, agent_name=self.name
            )
        return list(self.SYSTEM_TOOLS) + mcp_patterns

    async def run_sdk_session(
        self,
        *,
        prompt: str,
        working_directory: Path,
        mcp_context: dict[str, Any] | None = None,
        extra_allowed_tools: list[str] | None = None,
        graph_context: dict[str, Any] | None = None,
    ) -> str:
        """Drive a full Claude Agent SDK session and return its final result text.

        Streams every message from the SDK and writes structured logs:
        ``assistant_text``, ``tool_call``, ``tool_result``, ``sdk_finished``.
        Subclasses normally don't override this — they declare
        ``SDK_ALLOWED_TOOLS``, override ``build_mcp_servers``, and pass any
        per-task context here.

        Args:
            prompt: Initial user message handed to the SDK.
            working_directory: Repository checkout the SDK can edit.
            mcp_context: Free-form dict forwarded to ``build_mcp_servers``.
                Lets per-task data (auth tokens, etc.) reach the hook
                without sticking it on instance state.
            extra_allowed_tools: One-off additions to ``SDK_ALLOWED_TOOLS``
                for this single session, e.g. when a caller wants to grant
                a temporary tool without changing the class declaration.
            graph_context: Optional memory-graph context. When provided must
                contain ``task_node_id`` (int) and ``graph_writer``
                (GraphWriter). Both are forwarded to the logging helpers so
                every tool call and result is recorded in real time.
        """
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            UserMessage,
            query,
        )

        context = mcp_context or {}
        graph_writer = (graph_context or {}).get("graph_writer")
        task_node_id = (graph_context or {}).get("task_node_id")

        mcp_servers = await self.build_mcp_servers(context)
        in_process_servers = await self.build_in_process_mcp_servers(
            user_id=context.get("user_id"),
        )
        if in_process_servers:
            mcp_servers = {**mcp_servers, **in_process_servers}
        subagents = await self.build_subagents(context)

        allowed_tools = await self._load_allowed_tools(user_id=context.get("user_id"))
        if extra_allowed_tools:
            allowed_tools.extend(extra_allowed_tools)

        if subagents and "Agent" not in allowed_tools:
            raise RuntimeError(
                f"{type(self).__name__} declares subagents in build_subagents "
                f"but 'Agent' is missing from SDK_ALLOWED_TOOLS — Claude cannot "
                f"invoke subagents without it.",
            )

        options_kwargs: dict[str, Any] = {
            "allowed_tools": allowed_tools,
            "cwd": str(working_directory),
            "max_turns": self.SDK_MAX_TURNS,
            "permission_mode": self.SDK_PERMISSION_MODE,
            "model": self.SDK_MODEL,
            "mcp_servers": mcp_servers,
        }
        if self.SDK_SYSTEM_PROMPT is not None:
            options_kwargs["system_prompt"] = self.SDK_SYSTEM_PROMPT
        if subagents:
            options_kwargs["agents"] = subagents

        result_text = ""
        turn_count = 0

        try:
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(**options_kwargs),
            ):
                if isinstance(message, AssistantMessage):
                    turn_count += 1
                    await self._log_assistant_message(
                        message, turn_count,
                        graph_writer=graph_writer,
                        task_node_id=task_node_id,
                    )

                elif isinstance(message, UserMessage):
                    await self._log_tool_results(
                        message, turn_count,
                        graph_writer=graph_writer,
                    )

                elif isinstance(message, ResultMessage):
                    result_text = getattr(message, "result", "") or ""
                    self._logger.info(
                        "sdk_finished",
                        total_turns=turn_count,
                        cost_usd=getattr(message, "total_cost_usd", 0),
                        result_length=len(result_text),
                    )
        except Exception as exc:
            # The CLI sometimes exits with a non-zero code *after* emitting a
            # ResultMessage (e.g. when it bumps into max_turns or an internal
            # limit). The work Claude already did is committed to the working
            # directory — we want the caller to keep that. Only re-raise when
            # nothing useful was produced, so genuine startup failures are
            # not silently swallowed.
            if turn_count == 0:
                raise
            self._logger.warning(
                "sdk_session_aborted_with_partial_progress",
                turn_count=turn_count,
                partial_result_length=len(result_text),
                error=str(exc),
            )

        return result_text

    async def _log_assistant_message(
        self,
        message: Any,
        turn: int,
        *,
        graph_writer: Any | None = None,
        task_node_id: int | None = None,
    ) -> None:
        """Log text output and tool calls from an assistant turn.

        AssistantMessage.content is a list of ContentBlock (TextBlock,
        ThinkingBlock, ToolUseBlock, ...). We branch on attributes to stay
        forward-compatible with new block types.

        When ``graph_writer`` and ``task_node_id`` are provided, each tool
        call is also recorded into the memory graph in real time.
        """
        parent_tool_use_id = getattr(message, "parent_tool_use_id", None)
        for block in getattr(message, "content", []) or []:
            if hasattr(block, "text") and getattr(block, "text", ""):
                self._logger.info(
                    "assistant_text",
                    turn=turn,
                    parent_tool_use_id=parent_tool_use_id,
                    text=block.text[:500],
                )
            elif hasattr(block, "name") and hasattr(block, "input"):
                tool_name = getattr(block, "name", "unknown")
                tool_input = getattr(block, "input", {}) or {}
                tool_use_id = getattr(block, "id", None)
                detail = self._summarize_tool_call(tool_name, tool_input)
                self._logger.info(
                    "tool_call",
                    turn=turn,
                    parent_tool_use_id=parent_tool_use_id,
                    tool=tool_name,
                    tool_use_id=tool_use_id,
                    detail=detail,
                )
                if graph_writer is not None and task_node_id is not None and tool_use_id:
                    await graph_writer.record_tool_call(
                        task_node_id=task_node_id,
                        tool_name=tool_name,
                        tool_use_id=tool_use_id,
                        turn=turn,
                        detail=detail,
                        tool_input=tool_input,
                    )

    async def _log_tool_results(
        self,
        message: Any,
        turn: int,
        *,
        graph_writer: Any | None = None,
    ) -> None:
        """Log tool execution results returned to the SDK.

        UserMessage.content may be a string (initial prompt) or a list of
        ContentBlock — only ToolResultBlock entries are interesting here.

        When ``graph_writer`` is provided, each result patches the matching
        action node with its outcome and error status.
        """
        parent_tool_use_id = getattr(message, "parent_tool_use_id", None)
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            return
        for block in content:
            if not hasattr(block, "tool_use_id"):
                continue
            raw = getattr(block, "content", "")
            text = raw if isinstance(raw, str) else str(raw)
            is_error = bool(getattr(block, "is_error", False))
            tool_use_id = getattr(block, "tool_use_id", None)
            self._logger.info(
                "tool_result",
                turn=turn,
                parent_tool_use_id=parent_tool_use_id,
                is_error=is_error,
                result_length=len(text),
                tool_use_id=tool_use_id,
                content=text[:5000] if is_error else text[:5000],
            )
            if graph_writer is not None and tool_use_id:
                await graph_writer.patch_action_outcome(
                    tool_use_id=tool_use_id,
                    is_error=is_error,
                )

    @staticmethod
    def _summarize_tool_call(tool_name: str, tool_input: dict[str, Any]) -> str:
        """One-line human-readable summary of a tool call for structured logs."""
        match tool_name:
            case "Read":
                file_path = tool_input.get("file_path", "?")
                offset = tool_input.get("offset")
                return f"{file_path}:{offset}" if offset else file_path
            case "Edit":
                file_path = tool_input.get("file_path", "?")
                replaced_chars = len(tool_input.get("old_string", ""))
                return f"{file_path} (replacing {replaced_chars} chars)"
            case "Write":
                file_path = tool_input.get("file_path", "?")
                content_chars = len(tool_input.get("content", ""))
                return f"{file_path} ({content_chars} chars)"
            case "Glob":
                return tool_input.get("pattern", "?")
            case "Grep":
                return f"/{tool_input.get('pattern', '?')}/"
            case "Bash":
                return tool_input.get("command", "?")[:80]
            case "Agent":
                subagent_type = tool_input.get("subagent_type", "general-purpose")
                prompt_preview = tool_input.get("prompt", "?")[:60]
                return f"[{subagent_type}] {prompt_preview}"
            case _:
                return str(tool_input)[:80]
