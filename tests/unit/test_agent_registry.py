"""Tests for the AgentRegistry singleton.

Verifies agent registration, duplicate handling, lookup failures, and the
clear method used by tests to reset global state.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agents.base import BaseAgent
from src.engine.registry import AgentRegistry, AgentRegistrationError


# Minimal concrete agents for testing. These are intentionally simple.

class _FakeAgentA(BaseAgent):
    name = "fake_a"
    role = "Fake A"

    async def execute(self, state: Any) -> dict[str, Any]:
        return {}


class _FakeAgentB(BaseAgent):
    name = "fake_b"
    role = "Fake B"

    async def execute(self, state: Any) -> dict[str, Any]:
        return {}


class _AnotherFakeA(BaseAgent):
    """Different class but same name as _FakeAgentA — used to test conflicts."""
    name = "fake_a"
    role = "Another Fake A"

    async def execute(self, state: Any) -> dict[str, Any]:
        return {}


class TestAgentRegistry:
    def setup_method(self):
        """Fresh registry for every test so they don't interfere."""
        self.registry = AgentRegistry()

    async def test_register_and_get(self):
        self.registry.register(_FakeAgentA)
        result = self.registry.get("fake_a")
        assert result is _FakeAgentA

    async def test_duplicate_registration_same_class_ok(self):
        """Registering the exact same class twice should be a no-op."""
        self.registry.register(_FakeAgentA)
        self.registry.register(_FakeAgentA)  # should not raise

        assert self.registry.get("fake_a") is _FakeAgentA

    async def test_duplicate_name_different_class_raises(self):
        """Two different classes trying to claim the same name must fail."""
        self.registry.register(_FakeAgentA)

        with pytest.raises(AgentRegistrationError, match="already registered"):
            self.registry.register(_AnotherFakeA)

    async def test_get_missing_raises(self):
        """Looking up a name that was never registered must raise."""
        with pytest.raises(AgentRegistrationError, match="No agent registered"):
            self.registry.get("nonexistent")

    async def test_clear(self):
        """After clear(), all registrations should be gone."""
        self.registry.register(_FakeAgentA)
        self.registry.register(_FakeAgentB)

        self.registry.clear()

        assert self.registry.all() == {}
        with pytest.raises(AgentRegistrationError):
            self.registry.get("fake_a")

    async def test_all_returns_copy(self):
        """The all() method should list every registered agent."""
        self.registry.register(_FakeAgentA)
        self.registry.register(_FakeAgentB)

        all_agents = self.registry.all()
        assert len(all_agents) == 2
        assert "fake_a" in all_agents
        assert "fake_b" in all_agents
