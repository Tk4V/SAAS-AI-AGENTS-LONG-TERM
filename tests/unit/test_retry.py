"""Tests for the RetryPolicy async retry wrapper."""

from __future__ import annotations

import pytest

from src.utils.retry import RetryPolicy


class TestRetryPolicy:
    """Verify retry logic: immediate success, transient failures, and exhaustion."""

    async def test_succeeds_on_first_try(self) -> None:
        """Happy path: the function works immediately, no retries needed."""
        call_count = 0

        async def succeed() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        policy = RetryPolicy(max_attempts=3, base_delay=0.0, max_delay=0.0)
        result = await policy.run(succeed)

        assert result == "ok"
        assert call_count == 1

    async def test_retries_on_failure_then_succeeds(self) -> None:
        """The function fails twice then succeeds — we should get the result."""
        call_count = 0

        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient glitch")
            return "recovered"

        policy = RetryPolicy(
            max_attempts=5,
            base_delay=0.0,
            max_delay=0.0,
            retry_on=(ConnectionError,),
        )
        result = await policy.run(flaky)

        assert result == "recovered"
        assert call_count == 3

    async def test_exhausts_retries_raises_original(self) -> None:
        """When all attempts fail, the original exception type must surface."""
        async def always_fails() -> None:
            raise ValueError("permanent problem")

        policy = RetryPolicy(
            max_attempts=2,
            base_delay=0.0,
            max_delay=0.0,
            retry_on=(ValueError,),
        )

        with pytest.raises(ValueError, match="permanent problem"):
            await policy.run(always_fails)

    async def test_does_not_retry_non_matching_exceptions(self) -> None:
        """If the exception type is not in retry_on, it should propagate immediately."""
        call_count = 0

        async def wrong_error() -> None:
            nonlocal call_count
            call_count += 1
            raise TypeError("not retryable")

        policy = RetryPolicy(
            max_attempts=3,
            base_delay=0.0,
            max_delay=0.0,
            retry_on=(ConnectionError,),
        )

        with pytest.raises(TypeError):
            await policy.run(wrong_error)

        assert call_count == 1
