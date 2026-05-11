"""Unit tests for ``src.agents.chat.lifecycle.Lifecycle`` — pure timing logic."""

from __future__ import annotations

import time

from src.agents.chat.lifecycle import Lifecycle


class TestLifecycle:
    def test_fresh_lifecycle_has_no_expiry(self, monkeypatch: object) -> None:
        lc = Lifecycle(idle_timeout_sec=30, hard_timeout_sec=120)
        assert lc.expired() is None
        # remaining_sec uses min of (idle, hard) — both fresh, idle is smaller.
        assert lc.remaining_sec() > 25

    def test_idle_timer_fires_first(self, monkeypatch) -> None:
        lc = Lifecycle(idle_timeout_sec=10, hard_timeout_sec=1000)
        base = time.monotonic()
        # Pretend 15 seconds went by — past idle, not past hard.
        monkeypatch.setattr(time, "monotonic", lambda: base + 15)
        assert lc.expired() == "idle"

    def test_hard_timer_fires_when_idle_keeps_resetting(self, monkeypatch) -> None:
        lc = Lifecycle(idle_timeout_sec=10, hard_timeout_sec=60)
        base = time.monotonic()
        # Each user input resets the idle clock, but the hard clock keeps
        # going. After 65 s, even if idle was reset at 60 s, hard expires.
        monkeypatch.setattr(time, "monotonic", lambda: base + 59)
        lc.mark_user_input()
        monkeypatch.setattr(time, "monotonic", lambda: base + 65)
        assert lc.expired() == "hard"

    def test_mark_user_input_resets_idle(self, monkeypatch) -> None:
        lc = Lifecycle(idle_timeout_sec=10, hard_timeout_sec=1000)
        base = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: base + 9)
        assert lc.expired() is None  # 1s before idle expiry
        lc.mark_user_input()  # resets idle clock
        monkeypatch.setattr(time, "monotonic", lambda: base + 15)
        # 6 s since reset, still under the 10 s idle budget
        assert lc.expired() is None
        monkeypatch.setattr(time, "monotonic", lambda: base + 20)
        # 11 s since reset → idle fires
        assert lc.expired() == "idle"

    def test_remaining_sec_never_negative(self, monkeypatch) -> None:
        lc = Lifecycle(idle_timeout_sec=10, hard_timeout_sec=20)
        base = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: base + 100)
        assert lc.remaining_sec() == 0.0
