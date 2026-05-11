"""Timeout bookkeeping for a chat session.

Two clocks per session:

* idle — reset every time the user sends a new message. When it elapses
  the session ends gracefully — the user is presumed to have walked away.
* hard — set once at session start, never reset. Stops a single session
  from running for days even if the user keeps poking it.

Both are passed-through ``asyncio.wait_for`` style: the chat loop waits
on the input queue with the smaller of the two remaining budgets, and
when ``wait_for_message`` returns ``None`` it asks ``Lifecycle.expired()``
which one fired so the close reason can be reported.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from src.config.constants import (
    CHAT_SESSION_HARD_TIMEOUT_SEC,
    CHAT_SESSION_IDLE_TIMEOUT_SEC,
)


@dataclass
class Lifecycle:
    idle_timeout_sec: float = CHAT_SESSION_IDLE_TIMEOUT_SEC
    hard_timeout_sec: float = CHAT_SESSION_HARD_TIMEOUT_SEC

    def __post_init__(self) -> None:
        now = time.monotonic()
        self._started_at: float = now
        self._last_user_input_at: float = now

    def mark_user_input(self) -> None:
        """Reset the idle clock. Called when a new user message is consumed."""
        self._last_user_input_at = time.monotonic()

    def remaining_sec(self) -> float:
        """Seconds until the next timeout fires, whichever comes first.

        Returns 0.0 if either clock has already expired (caller should not
        block at all and instead call ``expired()`` to find out which).
        """
        now = time.monotonic()
        idle_left = self.idle_timeout_sec - (now - self._last_user_input_at)
        hard_left = self.hard_timeout_sec - (now - self._started_at)
        return max(0.0, min(idle_left, hard_left))

    def expired(self) -> str | None:
        """Returns ``"idle"`` / ``"hard"`` if either timer has elapsed,
        ``None`` otherwise. Caller decides how to report the close reason."""
        now = time.monotonic()
        if (now - self._started_at) >= self.hard_timeout_sec:
            return "hard"
        if (now - self._last_user_input_at) >= self.idle_timeout_sec:
            return "idle"
        return None
