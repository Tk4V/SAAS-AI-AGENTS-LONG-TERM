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

    # No default — concrete subclass MUST set this. __init_subclass__ enforces.
    SDK_ALLOWED_TOOLS: ClassVar[list[str]]

    SDK_MODEL: ClassVar[str] = "claude-sonnet-4-7"
    SDK_MAX_TURNS: ClassVar[int] = 50
    SDK_PERMISSION_MODE: ClassVar[str] = "acceptEdits"

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
                f"(got {type(tools).__name__}). See SDKAgent docstring.",
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

    async def run_sdk_session(
        self,
        *,
        prompt: str,
        working_directory: Path,
        mcp_context: dict[str, Any] | None = None,
        extra_allowed_tools: list[str] | None = None,
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
        """
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            UserMessage,
            query,
        )

        context = mcp_context or {}
        mcp_servers = await self.build_mcp_servers(context)
        subagents = await self.build_subagents(context)

        allowed_tools = list(self.SDK_ALLOWED_TOOLS)
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
        if subagents:
            options_kwargs["agents"] = subagents

        result_text = ""
        turn_count = 0

        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(**options_kwargs),
        ):
            if isinstance(message, AssistantMessage):
                turn_count += 1
                self._log_assistant_message(message, turn_count)

            elif isinstance(message, UserMessage):
                self._log_tool_results(message, turn_count)

            elif isinstance(message, ResultMessage):
                result_text = getattr(message, "result", "") or ""
                self._logger.info(
                    "sdk_finished",
                    total_turns=turn_count,
                    cost_usd=getattr(message, "total_cost_usd", 0),
                    result_length=len(result_text),
                )

        return result_text

    def _log_assistant_message(self, message: Any, turn: int) -> None:
        """Log text output and tool calls from an assistant turn."""
        text = getattr(message, "text", "") or ""
        if text:
            self._logger.info("assistant_text", turn=turn, text=text[:200])

        for tool_call in getattr(message, "tool_calls", []) or []:
            tool_name = getattr(tool_call, "name", "unknown")
            tool_input = getattr(tool_call, "input", {}) or {}
            self._logger.info(
                "tool_call",
                turn=turn,
                tool=tool_name,
                detail=self._summarize_tool_call(tool_name, tool_input),
            )

    def _log_tool_results(self, message: Any, turn: int) -> None:
        """Log tool execution results returned to the SDK."""
        for block in getattr(message, "content", []) or []:
            if hasattr(block, "tool_use_id"):
                self._logger.info(
                    "tool_result",
                    turn=turn,
                    is_error=getattr(block, "is_error", False),
                    result_length=len(str(getattr(block, "content", ""))),
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
