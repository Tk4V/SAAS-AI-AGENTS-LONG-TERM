"""Abstract LLM gateway used by every agent.

Agents never import a vendor SDK directly. They take an `LLMGateway` instance
through their constructor (or via DI in tests) and call its methods. This
keeps agents ignorant of which model is doing the work and lets us swap
Anthropic for another provider in `tools/llm/providers/` without touching
any agent code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

ChatRole = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A tool the LLM can call during a conversation."""

    name: str
    description: str
    input_schema: dict


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool invocation requested by the LLM."""

    id: str
    name: str
    input: dict


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The result of executing a tool, sent back to the LLM."""

    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: ChatRole
    content: str | list  # str for text, list for tool_use/tool_result blocks


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class CacheStats:
    """Tracks Anthropic prompt-caching token counts."""

    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_cached(self) -> int:
        return self.cache_creation_input_tokens + self.cache_read_input_tokens


@dataclass(frozen=True, slots=True)
class ChatResponse:
    text: str
    model: str
    usage: TokenUsage
    stop_reason: str | None = None
    raw: dict = field(default_factory=dict)
    cache_stats: CacheStats = field(default_factory=CacheStats)
    thinking_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMGateway(ABC):
    """Vendor-agnostic chat interface."""

    @abstractmethod
    async def chat(
        self,
        *,
        role: str,
        system: str,
        messages: list[ChatMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
        thinking: bool = False,
        thinking_budget: int = 4096,
        tools: list[ToolDefinition] | None = None,
    ) -> ChatResponse:
        """Single-shot completion. `role` selects the underlying model alias."""

    @abstractmethod
    async def stream(
        self,
        *,
        role: str,
        system: str,
        messages: list[ChatMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Token-by-token streaming. Yields text deltas as they arrive."""
