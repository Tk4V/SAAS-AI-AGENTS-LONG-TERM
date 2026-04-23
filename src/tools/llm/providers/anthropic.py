"""Anthropic-backed implementation of `LLMGateway`.

Wraps the async Anthropic SDK with our retry policy and turns the SDK's
response objects into the gateway's plain dataclasses so agents never see
vendor-specific types.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog
from anthropic import AsyncAnthropic
from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)

from src.common.exceptions import ExternalServiceError
from src.common.retry import RetryPolicy, RetryPresets
from src.config import Settings, get_settings
from src.tools.llm.gateway import (
    CacheStats, ChatMessage, ChatResponse, LLMGateway, TokenUsage,
    ToolCall, ToolDefinition,
)
from src.tools.llm.router import ModelRouter


class AnthropicLLMGateway(LLMGateway):
    """Talks to api.anthropic.com using the official async SDK."""

    _RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
        APIConnectionError,
        APITimeoutError,
        RateLimitError,
    )

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        router: ModelRouter | None = None,
        client: AsyncAnthropic | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._router = router or ModelRouter(settings=self._settings)
        self._client = client or AsyncAnthropic(
            api_key=self._settings.anthropic_api_key.get_secret_value(),
        )
        self._retry = retry_policy or RetryPresets.for_llm()
        self._logger = structlog.get_logger("clyde.llm.anthropic")

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
        model = self._router.model_for(role)
        payload = self._build_payload(
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
            thinking_budget=thinking_budget,
            tools=tools,
        )

        try:
            response = await self._retry.run(self._client.messages.create, **payload)
        except APIStatusError as exc:
            raise ExternalServiceError(
                "Anthropic returned a non-retryable status.",
                details={"status": exc.status_code, "message": str(exc)},
            ) from exc
        except self._RETRYABLE_ERRORS as exc:
            raise ExternalServiceError(
                "Anthropic call failed after exhausting retries.",
                details={"error": str(exc)},
            ) from exc

        text = self._extract_text(response)
        usage = TokenUsage(
            input_tokens=getattr(response.usage, "input_tokens", 0),
            output_tokens=getattr(response.usage, "output_tokens", 0),
        )

        # Pull out the model's internal reasoning when extended thinking is on
        thinking_text = ""
        if thinking:
            for block in getattr(response, "content", []) or []:
                if getattr(block, "type", "") == "thinking":
                    thinking_text = getattr(block, "text", "")

        # Extract tool_use blocks from the response content
        tool_calls: list[ToolCall] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", "") == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        # Extract prompt-caching stats from the response when available
        cache_creation = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
        cache_stats = CacheStats(
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
        )

        self._logger.info(
            "llm.chat.completed",
            role=role,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            stop_reason=response.stop_reason,
            thinking_enabled=thinking,
            tool_calls=len(tool_calls),
        )
        return ChatResponse(
            text=text,
            model=model,
            usage=usage,
            stop_reason=response.stop_reason,
            raw=response.model_dump() if hasattr(response, "model_dump") else {},
            cache_stats=cache_stats,
            thinking_text=thinking_text,
            tool_calls=tool_calls,
        )

    async def stream(
        self,
        *,
        role: str,
        system: str,
        messages: list[ChatMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        model = self._router.model_for(role)
        payload = self._build_payload(
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        try:
            async with self._client.messages.stream(**payload) as stream:
                async for text in stream.text_stream:
                    yield text
        except APIStatusError as exc:
            raise ExternalServiceError(
                "Anthropic returned a non-retryable status during streaming.",
                details={"status": exc.status_code, "message": str(exc)},
            ) from exc
        except self._RETRYABLE_ERRORS as exc:
            raise ExternalServiceError(
                "Anthropic streaming call failed.",
                details={"error": str(exc)},
            ) from exc

    def _build_payload(
        self,
        *,
        model: str,
        system: str,
        messages: list[ChatMessage],
        max_tokens: int | None,
        temperature: float | None,
        thinking: bool = False,
        thinking_budget: int = 4096,
        tools: list[ToolDefinition] | None = None,
    ) -> dict:
        # System prompt is sent as a content-block list so we can attach
        # cache_control. Anthropic caches this prefix for ~5 min, saving
        # 90% of input-token cost on repeated calls with the same prompt.

        # Build message list, handling both plain text and structured content
        # (tool_use blocks from assistant, tool_result arrays from user).
        api_messages = []
        for message in messages:
            if isinstance(message.content, str):
                api_messages.append({"role": message.role, "content": message.content})
            else:
                # Raw content: tool_use blocks or tool_result arrays
                api_messages.append({"role": message.role, "content": message.content})

        payload: dict = {
            "model": model,
            "max_tokens": max_tokens or self._settings.anthropic_max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": api_messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature

        # Attach tool definitions when the caller provides them
        if tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in tools
            ]

            # In a tool loop the first user message stays identical across all
            # turns while tool results get appended after it. Marking it as
            # cacheable lets Anthropic cache the system + first-message prefix,
            # which typically saves 80-90% of input tokens on turns 2+.
            if api_messages:
                first = api_messages[0]
                content = first.get("content")
                if isinstance(content, str):
                    api_messages[0] = {
                        "role": first["role"],
                        "content": [
                            {
                                "type": "text",
                                "text": content,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    }
                elif isinstance(content, list) and content:
                    # Already a list (turn 2+) — ensure the last text block
                    # in the first message has cache_control
                    last_block = content[-1]
                    if isinstance(last_block, dict) and last_block.get("type") == "text":
                        last_block["cache_control"] = {"type": "ephemeral"}

        # Extended thinking lets the model reason internally before answering.
        # Anthropic requires temperature=1 (the default) when thinking is on,
        # so we strip any explicit temperature to avoid a 400 error.
        if thinking:
            current_max = payload["max_tokens"]
            # Anthropic requires max_tokens > thinking.budget_tokens
            if thinking_budget >= current_max:
                payload["max_tokens"] = thinking_budget + 8192
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }
            payload.pop("temperature", None)

        return payload

    @staticmethod
    def _extract_text(response: object) -> str:
        """Concatenate every text block from the response, skipping tool use."""
        parts: list[str] = []
        for block in getattr(response, "content", []) or []:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
