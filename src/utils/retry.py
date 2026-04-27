"""Reusable retry policies for outbound calls.

Every external API in this project (Anthropic, GitHub, RDS) is wrapped
in a `RetryPolicy`. Centralising the retry behaviour means we get consistent
backoff, jitter, and structured logging for free.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryPolicy:
    """Builds tenacity retry decorators from a small set of explicit options.

    Use the same instance to wrap multiple calls — the policy is configuration,
    not state, so it is safe to share.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 30.0,
        retry_on: tuple[type[BaseException], ...] = (Exception,),
        reraise: bool = True,
        name: str = "default",
    ) -> None:
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._retry_on = retry_on
        self._reraise = reraise
        self._name = name

    async def run(
        self,
        func: Callable[..., Coroutine[Any, Any, T]],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute `func(*args, **kwargs)` with the configured retry policy."""
        attempt_index = 0
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential_jitter(initial=self._base_delay, max=self._max_delay),
                retry=retry_if_exception_type(self._retry_on),
                reraise=self._reraise,
            ):
                with attempt:
                    attempt_index = attempt.retry_state.attempt_number
                    if attempt_index > 1:
                        logger.warning(
                            "retry policy %s attempt %d/%d for %s",
                            self._name,
                            attempt_index,
                            self._max_attempts,
                            getattr(func, "__qualname__", str(func)),
                        )
                    return await func(*args, **kwargs)
        except RetryError as exc:
            raise exc.last_attempt.exception() from exc  # type: ignore[misc]


class RetryPresets:
    """Pre-configured retry policies for the typical call sites in the codebase.

    Each static method returns a fresh ``RetryPolicy`` tuned for a specific
    integration. Call the method once and reuse the returned policy across
    the lifetime of the component that needs it.
    """

    @staticmethod
    def for_github() -> RetryPolicy:
        """Return a retry policy tuned for GitHub API calls (3 attempts, 1-10 s backoff)."""
        return RetryPolicy(
            max_attempts=3,
            base_delay=1.0,
            max_delay=10.0,
            name="github",
        )

