"""Process-wide registry of every agent the engine can schedule.

Concrete agents register themselves at import time. The registry is queried
by `PipelineGraphBuilder` to resolve agent names declared in the pipeline
config into actual callable instances.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import TYPE_CHECKING, ClassVar

from src.common.exceptions import AppError

if TYPE_CHECKING:
    from src.agents.base import BaseAgent


class AgentRegistrationError(AppError):
    """Raised when a class registered as an agent is missing required attributes."""

    code = "agent_registration_error"
    http_status = 500


class AgentRegistry:
    """Singleton catalogue of agent classes keyed by their `name` attribute."""

    _instance: ClassVar["AgentRegistry | None"] = None

    def __init__(self) -> None:
        self._agents: dict[str, type[BaseAgent]] = {}

    @classmethod
    def instance(cls) -> "AgentRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, agent_class: type["BaseAgent"]) -> type["BaseAgent"]:
        """Decorator that adds the given class to the registry and returns it."""
        name = getattr(agent_class, "name", None)
        role = getattr(agent_class, "role", None)
        if not isinstance(name, str) or not name:
            raise AgentRegistrationError(
                f"{agent_class.__name__} must define a non-empty `name` class attribute.",
            )
        if not isinstance(role, str) or not role:
            raise AgentRegistrationError(
                f"{agent_class.__name__} must define a non-empty `role` class attribute.",
            )
        if inspect.isabstract(agent_class):
            raise AgentRegistrationError(
                f"{agent_class.__name__} is abstract and cannot be registered.",
            )

        existing = self._agents.get(name)
        if existing is not None and existing is not agent_class:
            raise AgentRegistrationError(
                f"Agent name {name!r} is already registered to {existing.__name__}.",
            )

        self._agents[name] = agent_class
        return agent_class

    def get(self, name: str) -> type["BaseAgent"]:
        try:
            return self._agents[name]
        except KeyError as exc:
            raise AgentRegistrationError(
                f"No agent registered under the name {name!r}.",
            ) from exc

    def all(self) -> dict[str, type["BaseAgent"]]:
        return dict(self._agents)

    def autoload(self, package_name: str = "src.agents.Development_team") -> None:
        """Walk the given package and import each `agent.py` module.

        Importing the module triggers the `@registry.register` decorator on
        the agent class defined inside, which adds it to `_agents`.
        """
        try:
            package = importlib.import_module(package_name)
        except ModuleNotFoundError as exc:
            raise AgentRegistrationError(
                f"Cannot autoload agents from missing package {package_name!r}.",
            ) from exc

        for _finder, module_name, _is_pkg in pkgutil.walk_packages(
            path=package.__path__,
            prefix=f"{package.__name__}.",
        ):
            if module_name.endswith(".agent"):
                importlib.import_module(module_name)

    def clear(self) -> None:
        """Drop all registrations. Intended for tests only."""
        self._agents.clear()
