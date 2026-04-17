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
class ChatMessage:
    role: ChatRole
    content: str


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class ChatResponse:
    text: str
    model: str
    usage: TokenUsage
    stop_reason: str | None = None
    raw: dict = field(default_factory=dict)


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
