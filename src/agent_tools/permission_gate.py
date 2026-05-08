"""In-process permission gate for human-in-the-loop tool approval.

Module-level dicts coordinate between the `can_use_tool` callback (running
inside the pipeline background task) and the HTTP resolve endpoint (called
from a different request coroutine).

This is safe because both sides run in the same uvicorn process — one
asyncio event loop, no cross-process IPC needed. If we ever move to
multi-worker uvicorn, replace these dicts with a Redis pub/sub channel.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

# task_id → {approval_id → asyncio.Event}
_pending: dict[UUID, dict[UUID, asyncio.Event]] = {}
# approval_id → decision (True = approved, False = denied)
_decisions: dict[UUID, bool] = {}


def register(task_id: UUID, approval_id: UUID) -> asyncio.Event:
    """Create an Event for this approval and track it under its task."""
    event = asyncio.Event()
    _pending.setdefault(task_id, {})[approval_id] = event
    return event


def resolve(approval_id: UUID, approved: bool) -> bool:
    """Set the decision and wake the waiting coroutine.

    Returns True if the approval_id was found (i.e. the pipeline is still
    waiting), False if it was not found (already resolved or timed out).
    """
    for task_events in _pending.values():
        if approval_id in task_events:
            _decisions[approval_id] = approved
            task_events[approval_id].set()
            return True
    return False


def get_decision(approval_id: UUID) -> bool:
    """Pop and return the resolved decision for an approval."""
    return _decisions.pop(approval_id, False)


def cleanup(task_id: UUID) -> None:
    """Remove all pending approvals for a finished task."""
    _pending.pop(task_id, None)
