"""Agents package.

The package intentionally does NOT re-export ``BaseAgent`` here — that
re-export used to trigger the ``services/__init__`` chain at import time
and turn into a circular import (``BaseAgent → services → task_service
→ OrchestratorAgent → SDKAgent → BaseAgent``). Every caller imports
the concrete agent module directly (``from src.agents.base_agent import
BaseAgent``), so the re-export carried no real benefit.
"""
