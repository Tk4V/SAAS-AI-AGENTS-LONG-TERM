"""Multi-turn tool-using loop for QA Engineer agent.

Used by QA to explore test structure before running tests.
Developer agent uses Claude Agent SDK instead.
"""

from __future__ import annotations

import asyncio
import structlog
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.tools.llm.gateway import (
    LLMGateway, ChatMessage, ToolDefinition, ToolCall, ToolResult,
)
from src.tools.agent_toolkit import AgentToolkit
from src.config.constants import TOOL_LOOP_DEFAULT_TOKEN_BUDGET


@dataclass
class ToolLoopResult:
    text: str
    turns_used: int
    edits_made: list[dict[str, Any]] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    budget_exhausted: bool = False
    peak_context_tokens: int = 0


class AgentToolExecutor:
    """Bridges tool calls from the LLM to AgentToolkit."""

    TOOL_DEFINITIONS: list[ToolDefinition]

    @classmethod
    def read_only_definitions(cls) -> list[ToolDefinition]:
        """Tools for read-only exploration (QA Engineer)."""
        return [d for d in cls.TOOL_DEFINITIONS if d.name in ("read_file", "grep", "list_files", "done")]

    def __init__(self, toolkit: AgentToolkit, repo_name: str, repo_path: Path) -> None:
        self._toolkit = toolkit
        self._repo_name = repo_name
        self._repo_path = repo_path
        self._done = False
        self._done_summary = ""
        self._logger = structlog.get_logger("clyde.tool_executor")

    @property
    def is_done(self) -> bool:
        return self._done

    @property
    def done_summary(self) -> str:
        return self._done_summary

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            handler = {
                "read_file": self._handle_read_file,
                "grep": self._handle_grep,
                "list_files": self._handle_list_files,
                "done": self._handle_done,
            }.get(call.name)

            if handler is None:
                return ToolResult(tool_use_id=call.id, content=f"Unknown tool: {call.name}", is_error=True)

            result_text = await handler(call.input)
            return ToolResult(tool_use_id=call.id, content=result_text)
        except Exception as e:
            self._logger.warning("tool_executor.error", tool=call.name, error=str(e))
            return ToolResult(tool_use_id=call.id, content=f"Error: {e!s}", is_error=True)

    async def _handle_read_file(self, params: dict) -> str:
        file_path = params["file_path"]
        start = params.get("start_line")
        end = params.get("end_line")
        if start and end:
            return self._toolkit.read_lines(self._repo_name, file_path, start, end)

        content = self._toolkit.read_file(self._repo_name, file_path)
        MAX_READ_CHARS = 12_000
        if len(content) > MAX_READ_CHARS:
            lines = content.splitlines()
            truncated = "\n".join(lines[:300])
            return (
                f"{truncated}\n\n"
                f"[FILE TRUNCATED — showing first 300 of {len(lines)} lines. "
                f"Use read_file with start_line/end_line to read specific sections.]"
            )
        return content

    async def _handle_grep(self, params: dict) -> str:
        matches = self._toolkit.grep(self._repo_name, params["pattern"], params.get("file_glob", "**/*"))
        if not matches:
            return "No matches found."
        lines = [f"{m.file_path}:{m.line_number}: {m.line_content}" for m in matches[:30]]
        return "\n".join(lines)

    async def _handle_list_files(self, params: dict) -> str:
        files = self._toolkit.list_files(self._repo_name, params.get("pattern", "**/*"))
        if not files:
            return "No files found."
        return "\n".join(files[:300])

    async def _handle_done(self, params: dict) -> str:
        self._done = True
        self._done_summary = params.get("summary", "")
        return "OK: done."


# -- Tool schemas ----------------------------------------------------------

AgentToolExecutor.TOOL_DEFINITIONS = [
    ToolDefinition(
        name="read_file",
        description="Read a file from the repository. Optionally specify line range.",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Relative path to the file"},
                "start_line": {"type": "integer", "description": "Start line (1-based, optional)"},
                "end_line": {"type": "integer", "description": "End line (inclusive, optional)"},
            },
            "required": ["file_path"],
        },
    ),
    ToolDefinition(
        name="grep",
        description="Search for a regex pattern in repository files.",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "file_glob": {"type": "string", "description": "Glob pattern to filter files", "default": "**/*"},
            },
            "required": ["pattern"],
        },
    ),
    ToolDefinition(
        name="list_files",
        description="List files in the repository matching a glob pattern.",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern", "default": "**/*"},
            },
        },
    ),
    ToolDefinition(
        name="done",
        description="Signal that you have finished. Call this with a summary.",
        input_schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Brief summary"},
            },
            "required": ["summary"],
        },
    ),
]


class ToolLoop:
    """Multi-turn conversation where the LLM calls tools. Used by QA Engineer."""

    MAX_TURNS = 15

    def __init__(
        self,
        *,
        llm: LLMGateway,
        executor: AgentToolExecutor,
        role: str = "qa_engineer",
        max_turns: int = MAX_TURNS,
        tools: list[ToolDefinition] | None = None,
        token_budget: int = TOOL_LOOP_DEFAULT_TOKEN_BUDGET,
    ) -> None:
        self._llm = llm
        self._executor = executor
        self._role = role
        self._max_turns = max_turns
        self._tools = tools or AgentToolExecutor.read_only_definitions()
        self._token_budget = token_budget
        self._logger = structlog.get_logger("clyde.tool_loop")

    async def run(self, *, system: str, initial_message: str) -> ToolLoopResult:
        messages: list[ChatMessage] = [ChatMessage(role="user", content=initial_message)]
        total_in = 0
        total_out = 0
        peak_context = 0

        for turn in range(1, self._max_turns + 1):
            response = await self._llm.chat(
                role=self._role,
                system=system,
                messages=messages,
                tools=self._tools,
            )

            total_in += response.usage.input_tokens
            total_out += response.usage.output_tokens
            peak_context = max(peak_context, response.usage.input_tokens)

            self._logger.info(
                "tool_loop.turn",
                turn=turn,
                stop_reason=response.stop_reason,
                tool_calls=len(response.tool_calls),
                tokens=response.usage.total,
            )

            if (total_in + total_out) > self._token_budget * 0.9:
                self._logger.warning("tool_loop.budget_exhausted", turn=turn, total_tokens=total_in + total_out)
                return ToolLoopResult(
                    text=self._executor.done_summary or "Budget exhausted",
                    turns_used=turn, total_input_tokens=total_in,
                    total_output_tokens=total_out, budget_exhausted=True,
                    peak_context_tokens=peak_context,
                )

            if not response.tool_calls:
                return ToolLoopResult(
                    text=response.text, turns_used=turn,
                    total_input_tokens=total_in, total_output_tokens=total_out,
                    peak_context_tokens=peak_context,
                )

            raw_content = response.raw.get("content", [])
            messages.append(ChatMessage(role="assistant", content=raw_content))

            tool_results = []
            results_list = await asyncio.gather(
                *[self._executor.execute(call) for call in response.tool_calls],
                return_exceptions=True,
            )
            for call, result in zip(response.tool_calls, results_list):
                if isinstance(result, Exception):
                    result = ToolResult(tool_use_id=call.id, content=f"Error: {result}", is_error=True)
                block: dict[str, Any] = {"type": "tool_result", "tool_use_id": call.id, "content": result.content}
                if result.is_error:
                    block["is_error"] = True
                tool_results.append(block)

                self._logger.info(
                    "tool_loop.tool_executed",
                    turn=turn, tool=call.name,
                    detail=self._tool_detail(call),
                    is_error=result.is_error,
                    result_len=len(result.content),
                )

            messages.append(ChatMessage(role="user", content=tool_results))

            if self._executor.is_done:
                return ToolLoopResult(
                    text=self._executor.done_summary, turns_used=turn,
                    total_input_tokens=total_in, total_output_tokens=total_out,
                    peak_context_tokens=peak_context,
                )

        self._logger.warning("tool_loop.max_turns_reached", max_turns=self._max_turns)
        return ToolLoopResult(
            text="Max turns reached", turns_used=self._max_turns,
            total_input_tokens=total_in, total_output_tokens=total_out,
            peak_context_tokens=peak_context,
        )

    @staticmethod
    def _tool_detail(call: ToolCall) -> str:
        inp = call.input
        match call.name:
            case "read_file":
                path = inp.get("file_path", "?")
                start = inp.get("start_line")
                end = inp.get("end_line")
                return f"{path}:{start}-{end}" if start and end else path
            case "grep":
                return f"/{inp.get('pattern', '?')}/ in {inp.get('file_glob', '**/*')}"
            case "list_files":
                return inp.get("pattern", "**/*")
            case "done":
                return inp.get("summary", "")[:80]
            case _:
                return str(inp)[:80]
