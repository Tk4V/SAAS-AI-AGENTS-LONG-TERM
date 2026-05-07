"""GraphWriter — real-time writer for the agent memory graph.

Persists task nodes, action nodes, entity nodes, and typed edges into the
``memory_nodes`` / ``memory_edges`` tables during SDK session execution.

Design constraints
------------------
* Every public method opens and commits its own short-lived DB session so
  that partial progress is preserved even when a task fails mid-run. A
  long-lived session for the duration of a multi-hour SDK session would
  hold a transaction open indefinitely.
* Every public method is failure-isolated: exceptions are caught, logged,
  and swallowed. A graph write failure must never affect task execution.
* Methods return ``None`` on failure so callers can skip dependent writes
  (e.g. skip edge creation if action node creation failed).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from src.db.models.memory_graph import MemoryEdge, MemoryNode
from src.db.session import db
from src.tools.custom.memory.entity_extractor import ExtractedEntity, extract_entities

_log = logging.getLogger(__name__)


class GraphWriter:
    """Write agent activity into the memory graph in real time."""

    # ── task lifecycle ────────────────────────────────────────────────────────

    async def create_task_node(
        self,
        *,
        task_id: str | UUID,
        user_id: int,
        agent_id: str | UUID,
        description: str,
        attempt: int,
    ) -> int | None:
        """Create a task node at the start of execution. Returns the node id."""
        try:
            async with db.session_scope() as session:
                node = MemoryNode(
                    node_type="task",
                    properties={
                        "task_id": str(task_id),
                        "user_id": user_id,
                        "agent_id": str(agent_id),
                        "description": description,
                        "status": "running",
                        "attempt": attempt,
                    },
                )
                session.add(node)
                await session.flush()
                return node.id
        except Exception:
            _log.warning("graph_writer.create_task_node.failed", exc_info=True)
            return None

    async def finish_task(self, *, task_node_id: int | None, status: str) -> None:
        """Update a task node's status and completed_at timestamp."""
        if task_node_id is None:
            return
        try:
            patch = json.dumps({"status": status, "completed_at": _utcnow()})
            async with db.session_scope() as session:
                await session.execute(
                    sa.text(
                        "UPDATE memory_nodes "
                        "SET properties = properties || :patch ::jsonb, "
                        "    updated_at  = now() "
                        "WHERE id = :node_id"
                    ),
                    {"patch": patch, "node_id": task_node_id},
                )
        except Exception:
            _log.warning("graph_writer.finish_task.failed", exc_info=True)

    # ── tool call recording ───────────────────────────────────────────────────

    async def record_tool_call(
        self,
        *,
        task_node_id: int,
        tool_name: str,
        tool_use_id: str,
        turn: int,
        detail: str,
        tool_input: dict[str, Any],
    ) -> int | None:
        """Create action node + entity nodes + edges in a single call.

        Primary write method called from ``_log_assistant_message``.
        Returns the action node id, or None if creation failed.
        """
        action_id = await self._create_action_node(
            task_node_id=task_node_id,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            turn=turn,
            detail=detail,
        )
        if action_id is None:
            return None

        for entity in extract_entities(tool_name, tool_input):
            entity_id = await self.upsert_entity(kind=entity.kind, identifier=entity.identifier)
            if entity_id is not None:
                await self._create_edge(
                    source_id=action_id,
                    target_id=entity_id,
                    edge_type=entity.edge_type,
                )

        return action_id

    async def patch_action_outcome(self, *, tool_use_id: str, is_error: bool) -> None:
        """Patch outcome and is_error onto an action node after the tool returns."""
        try:
            outcome = "error" if is_error else "success"
            patch = json.dumps({"outcome": outcome, "is_error": is_error})
            async with db.session_scope() as session:
                await session.execute(
                    sa.text(
                        "UPDATE memory_nodes "
                        "SET properties = properties || :patch ::jsonb, "
                        "    updated_at  = now() "
                        "WHERE node_type = 'action' "
                        "  AND properties->>'tool_use_id' = :tool_use_id"
                    ),
                    {"patch": patch, "tool_use_id": tool_use_id},
                )
        except Exception:
            _log.warning("graph_writer.patch_action_outcome.failed", exc_info=True)

    async def upsert_entity(self, *, kind: str, identifier: str) -> int | None:
        """Find or create an entity node. Returns the node id."""
        try:
            async with db.session_scope() as session:
                existing = await session.execute(
                    sa.text("""
                        SELECT id FROM memory_nodes
                        WHERE node_type = 'entity'
                          AND properties->>'kind'       = :kind
                          AND properties->>'identifier' = :identifier
                    """),
                    {"kind": kind, "identifier": identifier},
                )
                row = existing.fetchone()
                if row:
                    return row[0]

                result = await session.execute(
                    sa.text("""
                        INSERT INTO memory_nodes (node_type, properties, created_at, updated_at)
                        VALUES (
                            'entity',
                            jsonb_build_object(
                                'kind',       CAST(:kind AS TEXT),
                                'identifier', CAST(:identifier AS TEXT)
                            ),
                            now(), now()
                        )
                        RETURNING id
                    """),
                    {"kind": kind, "identifier": identifier},
                )
                row = result.fetchone()
                return row[0] if row else None
        except Exception:
            _log.warning("graph_writer.upsert_entity.failed", exc_info=True)
            return None

    # ── private helpers ───────────────────────────────────────────────────────

    async def _create_action_node(
        self,
        *,
        task_node_id: int,
        tool_name: str,
        tool_use_id: str,
        turn: int,
        detail: str,
    ) -> int | None:
        try:
            async with db.session_scope() as session:
                node = MemoryNode(
                    node_type="action",
                    properties={
                        "tool_name": tool_name,
                        "tool_use_id": tool_use_id,
                        "turn": turn,
                        "detail": detail,
                        "outcome": None,
                        "is_error": None,
                    },
                )
                session.add(node)
                await session.flush()
                action_id = node.id

                await session.execute(
                    insert(MemoryEdge).values(
                        source_id=task_node_id,
                        target_id=action_id,
                        edge_type="executed",
                        weight=1.0,
                    ).on_conflict_do_update(
                        constraint="uq_memory_edges_source_target_type",
                        set_={"weight": MemoryEdge.weight + 1.0},
                    )
                )
                return action_id
        except Exception:
            _log.warning("graph_writer.create_action_node.failed", exc_info=True)
            return None

    async def _create_edge(
        self,
        *,
        source_id: int,
        target_id: int,
        edge_type: str,
        weight: float = 1.0,
    ) -> None:
        try:
            async with db.session_scope() as session:
                await session.execute(
                    insert(MemoryEdge).values(
                        source_id=source_id,
                        target_id=target_id,
                        edge_type=edge_type,
                        weight=weight,
                    ).on_conflict_do_update(
                        constraint="uq_memory_edges_source_target_type",
                        set_={"weight": MemoryEdge.weight + weight},
                    )
                )
        except Exception:
            _log.warning("graph_writer.create_edge.failed", exc_info=True)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()
