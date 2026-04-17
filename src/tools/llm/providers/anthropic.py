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
from src.tools.llm.gateway import ChatMessage, ChatResponse, LLMGateway, TokenUsage
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
    ) -> ChatResponse:
        model = self._router.model_for(role)
        payload = self._build_payload(
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
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
        self._logger.info(
            "llm.chat.completed",
            role=role,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            stop_reason=response.stop_reason,
        )
        return ChatResponse(
            text=text,
            model=model,
            usage=usage,
            stop_reason=response.stop_reason,
            raw=response.model_dump() if hasattr(response, "model_dump") else {},
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
    ) -> dict:
        payload: dict = {
            "model": model,
            "max_tokens": max_tokens or self._settings.anthropic_max_tokens,
            "system": system,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
        }
        if temperature is not None:
            payload["temperature"] = temperature
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
