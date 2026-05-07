"""In-process memory MCP server for the orchestrator.

Builds an SDK-native MCP server (no external process, no HTTP, no OAuth)
that gives the orchestrator read/write access to its own memory graph.

Factory pattern: tools are constructed per-session so each handler closes
over the correct ``user_id`` and ``task_node_id`` without relying on
module-level state.

Usage in ``build_mcp_servers``::

    from src.tools.custom.memory.mcp_server import create_memory_mcp_server

    servers["memory"] = create_memory_mcp_server(
        user_id=user_id,
        task_node_id=task_node_id,
    )
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server

from src.tools.custom.memory.retrieval import MemoryRetrieval


def create_memory_mcp_server(
    *,
    user_id: int,
    task_node_id: int | None,
) -> McpSdkServerConfig:
    """Return a configured in-process memory MCP server for this session.

    Each call creates a fresh ``MemoryRetrieval`` instance and wraps it in
    four ``SdkMcpTool`` objects that close over ``user_id`` and
    ``task_node_id``.  The server is registered under the name ``"memory"``
    so tool names are exposed as ``mcp__memory__*``.
    """
    retrieval = MemoryRetrieval()

    # ── memory_recall ─────────────────────────────────────────────────────────

    async def _recall(args: dict[str, Any]) -> dict[str, Any]:
        result = await retrieval.recall(
            user_id=user_id,
            query=args["query"],
            limit=int(args.get("limit", 5)),
        )
        return {"content": [{"type": "text", "text": result}]}

    recall_tool = SdkMcpTool(
        name="memory_recall",
        description=(
            "Hybrid full-text + vector search over past completed tasks for this user. "
            "Returns a formatted memory block: task descriptions, files touched, APIs "
            "called, and tool-call counts. Call this BEFORE starting work to surface "
            "relevant context from prior sessions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-form description of the current task or topic to recall.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of past tasks to return (default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        handler=_recall,
    )

    # ── memory_search_entity ──────────────────────────────────────────────────

    async def _search_entity(args: dict[str, Any]) -> dict[str, Any]:
        result = await retrieval.search_entity(
            user_id=user_id,
            kind=args["kind"],
            identifier=args["identifier"],
        )
        return {"content": [{"type": "text", "text": result}]}

    search_entity_tool = SdkMcpTool(
        name="memory_search_entity",
        description=(
            "Return all past tasks that touched a specific entity (file, repo, api, "
            "subagent, or channel). Useful to understand the full history of a "
            "particular file or external resource before modifying it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "Entity type: 'file', 'repo', 'api', 'subagent', or 'channel'.",
                    "enum": ["file", "repo", "api", "subagent", "channel"],
                },
                "identifier": {
                    "type": "string",
                    "description": (
                        "Entity identifier — file path, repo slug (owner/repo), "
                        "API name, subagent name, or channel ID."
                    ),
                },
            },
            "required": ["kind", "identifier"],
        },
        handler=_search_entity,
    )

    # ── memory_list_recent ────────────────────────────────────────────────────

    async def _list_recent(args: dict[str, Any]) -> dict[str, Any]:
        result = await retrieval.list_recent(
            user_id=user_id,
            limit=int(args.get("limit", 10)),
        )
        return {"content": [{"type": "text", "text": result}]}

    list_recent_tool = SdkMcpTool(
        name="memory_list_recent",
        description=(
            "Return the N most recent completed or failed tasks for this user, "
            "ordered newest first. Useful for a quick orientation before diving "
            "into task-specific recall."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of tasks to return (default 10).",
                    "default": 10,
                },
            },
            "required": [],
        },
        handler=_list_recent,
    )

    # ── memory_annotate ───────────────────────────────────────────────────────

    async def _annotate(args: dict[str, Any]) -> dict[str, Any]:
        result = await retrieval.annotate_task(
            task_node_id=task_node_id,
            note=args["note"],
        )
        return {"content": [{"type": "text", "text": result}]}

    annotate_tool = SdkMcpTool(
        name="memory_annotate",
        description=(
            "Append a freeform note to the current task's memory node. "
            "Use this to record a key decision, an unexpected finding, or a "
            "warning for future tasks that work in the same area."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": "The note to append to the current task record.",
                },
            },
            "required": ["note"],
        },
        handler=_annotate,
    )

    # ── assemble server ───────────────────────────────────────────────────────

    return create_sdk_mcp_server(
        name="memory",
        version="1.0.0",
        tools=[recall_tool, search_entity_tool, list_recent_tool, annotate_tool],
    )
