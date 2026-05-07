"""MemoryRetrieval — read path for the agent memory graph.

Implements hybrid search (full-text + vector where available) with RRF merge
and 2-hop graph expansion to reconstruct what past tasks actually did.

Vector leg is skipped gracefully when no embeddings exist (no embedding
service is wired yet) — full-text alone still provides meaningful recall.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import sqlalchemy as sa

from src.db.session import db

_log = logging.getLogger(__name__)

_RRF_K = 60  # standard constant for Reciprocal Rank Fusion


class MemoryRetrieval:
    """Query the memory graph and format results for the orchestrator."""

    # ── primary retrieval ─────────────────────────────────────────────────────

    async def recall(self, *, user_id: int, query: str, limit: int = 5) -> str:
        """Hybrid search + RRF + graph expansion.

        Returns a formatted memory block string ready to be returned as a
        tool result to the orchestrator.
        """
        try:
            async with db.session_scope() as session:
                user_id_str = str(user_id)

                # ── full-text leg ─────────────────────────────────────────────
                ft_rows = (await session.execute(
                    sa.text("""
                        SELECT id,
                               ts_rank(search_text, plainto_tsquery('english', :q)) AS score
                        FROM   memory_nodes
                        WHERE  node_type = 'task'
                          AND  properties->>'user_id' = :uid
                          AND  properties->>'status'  = 'completed'
                          AND  search_text @@ plainto_tsquery('english', :q)
                        ORDER  BY score DESC
                        LIMIT  20
                    """),
                    {"q": query, "uid": user_id_str},
                )).fetchall()

                # ── vector leg (skip if no embeddings exist) ──────────────────
                vec_rows: list[Any] = []
                has_embeddings = (await session.execute(
                    sa.text("""
                        SELECT 1 FROM memory_nodes
                        WHERE node_type = 'task'
                          AND embedding IS NOT NULL
                        LIMIT 1
                    """),
                )).fetchone()

                # Vector leg is a placeholder — embedding generation not yet
                # wired. When an embedding service is available, replace the
                # block below with a real cosine-similarity query.
                # vec_rows = [...]  # populate with (id, score) tuples

                # ── RRF merge ─────────────────────────────────────────────────
                scores: dict[int, float] = defaultdict(float)

                for rank, row in enumerate(ft_rows, start=1):
                    scores[row[0]] += 1.0 / (_RRF_K + rank)

                for rank, row in enumerate(vec_rows, start=1):
                    scores[row[0]] += 1.0 / (_RRF_K + rank)

                if not scores:
                    return "No relevant past tasks found."

                top_ids = sorted(scores, key=lambda k: scores[k], reverse=True)[:limit]

                # ── task properties ───────────────────────────────────────────
                task_rows = (await session.execute(
                    sa.text("""
                        SELECT id, properties, created_at
                        FROM   memory_nodes
                        WHERE  id = ANY(:ids)
                        ORDER  BY created_at DESC
                    """),
                    {"ids": top_ids},
                )).fetchall()

                # ── graph expansion: actions + entities (2 hops) ──────────────
                expansion = (await session.execute(
                    sa.text("""
                        -- Hop 1: task → actions
                        SELECT e1.source_id AS task_id,
                               'action'     AS kind,
                               a.properties
                        FROM   memory_edges e1
                        JOIN   memory_nodes a ON a.id = e1.target_id
                        WHERE  e1.source_id = ANY(:ids)
                          AND  e1.edge_type = 'executed'

                        UNION ALL

                        -- Hop 2: task → actions → entities
                        SELECT e1.source_id AS task_id,
                               n.node_type   AS kind,
                               n.properties
                        FROM   memory_edges e1
                        JOIN   memory_edges e2 ON e2.source_id = e1.target_id
                        JOIN   memory_nodes n  ON n.id = e2.target_id
                        WHERE  e1.source_id = ANY(:ids)
                          AND  e1.edge_type = 'executed'
                          AND  e2.edge_type IN ('read', 'wrote', 'called', 'targeted')
                    """),
                    {"ids": top_ids},
                )).fetchall()

            return _format_recall(task_rows, expansion)

        except Exception:
            _log.warning("memory_retrieval.recall.failed", exc_info=True)
            return "Memory recall unavailable."

    # ── entity history ────────────────────────────────────────────────────────

    async def search_entity(
        self,
        *,
        user_id: int,
        kind: str,
        identifier: str,
    ) -> str:
        """Return past tasks that touched a specific entity."""
        try:
            async with db.session_scope() as session:
                rows = (await session.execute(
                    sa.text("""
                        SELECT DISTINCT task.properties, task.created_at
                        FROM   memory_nodes  entity
                        JOIN   memory_edges  e_action  ON e_action.target_id  = entity.id
                        JOIN   memory_nodes  action    ON action.id = e_action.source_id
                        JOIN   memory_edges  e_task    ON e_task.target_id = action.id
                        JOIN   memory_nodes  task      ON task.id = e_task.source_id
                        WHERE  entity.node_type = 'entity'
                          AND  entity.properties->>'kind'       = :kind
                          AND  entity.properties->>'identifier' = :identifier
                          AND  task.node_type = 'task'
                          AND  task.properties->>'user_id' = :uid
                        ORDER  BY task.created_at DESC
                        LIMIT  10
                    """),
                    {"kind": kind, "identifier": identifier, "uid": str(user_id)},
                )).fetchall()

            if not rows:
                return f"No past tasks found that touched {kind}:{identifier}."

            lines = [f"Tasks that touched {kind}:{identifier}:", ""]
            for row in rows:
                props = row[0]
                desc = props.get("description", "")[:120]
                status = props.get("status", "?")
                lines.append(f"  [{status}] {desc}")
            return "\n".join(lines)

        except Exception:
            _log.warning("memory_retrieval.search_entity.failed", exc_info=True)
            return "Entity search unavailable."

    # ── recent tasks ──────────────────────────────────────────────────────────

    async def list_recent(self, *, user_id: int, limit: int = 10) -> str:
        """Return the N most recently completed tasks for this user."""
        try:
            async with db.session_scope() as session:
                rows = (await session.execute(
                    sa.text("""
                        SELECT properties, created_at
                        FROM   memory_nodes
                        WHERE  node_type = 'task'
                          AND  properties->>'user_id' = :uid
                          AND  properties->>'status' IN ('completed', 'failed')
                        ORDER  BY created_at DESC
                        LIMIT  :lim
                    """),
                    {"uid": str(user_id), "lim": limit},
                )).fetchall()

            if not rows:
                return "No past tasks found."

            lines = ["Recent tasks:", ""]
            for row in rows:
                props = row[0]
                desc = props.get("description", "")[:120]
                status = props.get("status", "?")
                attempt = props.get("attempt", 0)
                lines.append(f"  [{status}] (attempt {attempt}) {desc}")
            return "\n".join(lines)

        except Exception:
            _log.warning("memory_retrieval.list_recent.failed", exc_info=True)
            return "Recent tasks unavailable."

    # ── annotation ───────────────────────────────────────────────────────────

    async def annotate_task(self, *, task_node_id: int | None, note: str) -> str:
        """Append a freeform note to the current task node."""
        if task_node_id is None:
            return "OK (note not stored — task node unavailable)"
        try:
            async with db.session_scope() as session:
                await session.execute(
                    sa.text("""
                        UPDATE memory_nodes
                        SET    properties = jsonb_set(
                                   properties,
                                   '{notes}',
                                   coalesce(properties->'notes', '[]'::jsonb)
                                   || to_jsonb(:note::text)
                               ),
                               updated_at = now()
                        WHERE  id = :node_id
                    """),
                    {"note": note, "node_id": task_node_id},
                )
            return "OK"
        except Exception:
            _log.warning("memory_retrieval.annotate_task.failed", exc_info=True)
            return "Annotation unavailable."


# ── formatting ────────────────────────────────────────────────────────────────

def _format_recall(
    task_rows: list[Any],
    expansion_rows: list[Any],
) -> str:
    """Build a compact memory block from task rows + expansion results."""
    # Group expansion by task_id
    by_task: dict[int, dict[str, list[str]]] = defaultdict(lambda: {
        "files": [], "apis": [], "subagents": [], "tool_calls": [],
    })

    tool_call_counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for row in expansion_rows:
        task_id: int = row[0]
        kind: str = row[1]
        props: dict = row[2]

        if kind == "action":
            tool_name = props.get("tool_name", "?")
            tool_call_counts[task_id][tool_name] += 1
        elif kind == "entity":
            entity_kind = props.get("kind", "")
            identifier = props.get("identifier", "")
            if entity_kind == "file":
                by_task[task_id]["files"].append(identifier)
            elif entity_kind == "api":
                by_task[task_id]["apis"].append(identifier)
            elif entity_kind == "subagent":
                by_task[task_id]["subagents"].append(identifier)

    if not task_rows:
        return "No relevant past tasks found."

    lines = ["=== Relevant memory from past tasks ===", ""]

    for row in task_rows:
        task_id: int = row[0]
        props: dict = row[1]

        desc = props.get("description", "")[:120]
        status = props.get("status", "?")
        attempt = props.get("attempt", 0)
        lines.append(f'[Task: "{desc}" — {status}, attempt {attempt}]')

        bucket = by_task.get(task_id, {})

        files = list(dict.fromkeys(bucket.get("files", [])))[:8]
        apis = list(dict.fromkeys(bucket.get("apis", [])))
        subagents = list(dict.fromkeys(bucket.get("subagents", [])))
        counts = tool_call_counts.get(task_id, {})

        if files:
            lines.append(f"  Files touched: {', '.join(files)}")
        if apis:
            lines.append(f"  APIs called:   {', '.join(apis)}")
        if subagents:
            lines.append(f"  Subagents:     {', '.join(subagents)}")
        if counts:
            summary = ", ".join(
                f"{t} × {n}" for t, n in sorted(counts.items(), key=lambda x: -x[1])
            )
            lines.append(f"  Tool calls:    {summary}")
        lines.append("")

    lines.append("=======================================")
    return "\n".join(lines)
