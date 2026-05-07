"""GraphWriter — real-time writer for the agent memory graph.

Persists task nodes, action nodes, entity nodes, and typed edges into the
``memory_nodes`` / ``memory_edges`` tables during SDK session execution.

Design constraints
------------------
* Every public method is failure-isolated: exceptions are caught, logged,
  and swallowed. A graph write failure must never affect task execution.
* Methods return ``None`` on failure so callers can skip dependent work
  (e.g. skip edge creation if action node creation failed).
* Session management is the caller's responsibility — GraphWriter only
  calls ``flush()`` (never ``commit()``), matching the repository convention
  in ``src/db/queries/``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.memory_graph import MemoryEdge, MemoryNode
from src.memory.entity_extractor import ExtractedEntity, extract_entities

_log = logging.getLogger(__name__)


class GraphWriter:
    """Write agent activity into the memory graph in real time."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
            self._session.add(node)
            await self._session.flush()
            return node.id
        except Exception:
            _log.warning("graph_writer.create_task_node.failed", exc_info=True)
            return None

    async def finish_task(self, *, task_node_id: int, status: str) -> None:
        """Update a task node's status and completed_at timestamp."""
        try:
            patch = json.dumps({"status": status, "completed_at": _utcnow()})
            await self._session.execute(
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

    async def create_action_node(
        self,
        *,
        task_node_id: int,
        tool_name: str,
        tool_use_id: str,
        turn: int,
        detail: str,
    ) -> int | None:
        """Create an action node and wire it to the task with an 'executed' edge."""
        try:
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
            self._session.add(node)
            await self._session.flush()
            await self.create_edge(
                source_id=task_node_id,
                target_id=node.id,
                edge_type="executed",
            )
            return node.id
        except Exception:
            _log.warning("graph_writer.create_action_node.failed", exc_info=True)
            return None

    async def patch_action_outcome(
        self,
        *,
        tool_use_id: str,
        is_error: bool,
    ) -> None:
        """Patch outcome and is_error onto an action node after the tool returns."""
        try:
            outcome = "error" if is_error else "success"
            patch = json.dumps({"outcome": outcome, "is_error": is_error})
            await self._session.execute(
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

        This is the primary write method called from ``_log_assistant_message``.
        Returns the action node id, or None if creation failed.
        """
        action_id = await self.create_action_node(
            task_node_id=task_node_id,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            turn=turn,
            detail=detail,
        )
        if action_id is None:
            return None

        entities: list[ExtractedEntity] = extract_entities(tool_name, tool_input)
        for entity in entities:
            entity_id = await self.upsert_entity(
                kind=entity.kind,
                identifier=entity.identifier,
            )
            if entity_id is not None:
                await self.create_edge(
                    source_id=action_id,
                    target_id=entity_id,
                    edge_type=entity.edge_type,
                )

        return action_id

    async def upsert_entity(self, *, kind: str, identifier: str) -> int | None:
        """Find or create an entity node. Returns the node id.

        Uses INSERT ... ON CONFLICT DO NOTHING against the partial unique index
        ``idx_memory_nodes_entity_identity`` on (kind, identifier) WHERE
        node_type = 'entity'. Falls back to a SELECT when the row already exists
        (ON CONFLICT DO NOTHING returns no rows).
        """
        try:
            stmt = (
                insert(MemoryNode)
                .values(
                    node_type="entity",
                    properties={"kind": kind, "identifier": identifier},
                )
                .on_conflict_do_nothing(
                    index_elements=None,
                    index_where=None,
                )
                .returning(MemoryNode.id)
            )
            result = (await self._session.execute(stmt)).scalar_one_or_none()

            if result is not None:
                return result

            # Row already existed — fetch its id.
            existing = (
                await self._session.execute(
                    sa.select(MemoryNode.id).where(
                        MemoryNode.node_type == "entity",
                        sa.cast(MemoryNode.properties["kind"], sa.Text) == kind,
                        sa.cast(MemoryNode.properties["identifier"], sa.Text) == identifier,
                    )
                )
            ).scalar_one_or_none()
            return existing
        except Exception:
            _log.warning("graph_writer.upsert_entity.failed", exc_info=True)
            return None

    async def create_edge(
        self,
        *,
        source_id: int,
        target_id: int,
        edge_type: str,
        weight: float = 1.0,
    ) -> None:
        """Insert an edge, incrementing weight if it already exists."""
        try:
            stmt = (
                insert(MemoryEdge)
                .values(
                    source_id=source_id,
                    target_id=target_id,
                    edge_type=edge_type,
                    weight=weight,
                )
                .on_conflict_do_update(
                    constraint="uq_memory_edges_source_target_type",
                    set_={"weight": MemoryEdge.weight + weight},
                )
            )
            await self._session.execute(stmt)
        except Exception:
            _log.warning("graph_writer.create_edge.failed", exc_info=True)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()
