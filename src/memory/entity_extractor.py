"""Pure entity extraction from agent tool calls.

Maps (tool_name, tool_input) to a list of ExtractedEntity objects that
GraphWriter uses to upsert entity nodes and create typed edges.

No database imports — this module is fully unit-testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExtractedEntity:
    kind: str        # "file" | "repo" | "api" | "subagent" | "channel"
    identifier: str
    edge_type: str   # "read" | "wrote" | "called" | "targeted"


def extract_entities(
    tool_name: str,
    tool_input: dict[str, Any],
) -> list[ExtractedEntity]:
    """Return all entities referenced by a single tool invocation.

    One tool call may produce multiple entities — for example,
    ``mcp__github__create_pull_request`` yields both an ``api:github``
    entity and a ``repo:owner/repo`` entity.

    ``mcp__memory__*`` tools are intentionally ignored to avoid
    self-referential graph noise.
    """
    match tool_name:
        case "Read":
            return _file(tool_input.get("file_path", ""), "read")

        case "Edit":
            return _file(tool_input.get("file_path", ""), "wrote")

        case "Write":
            return _file(tool_input.get("file_path", ""), "wrote")

        case "Glob":
            path = tool_input.get("path", "")
            return _file(path, "read") if path else []

        case "Grep":
            path = tool_input.get("path", "")
            return _file(path, "read") if path else []

        case "Bash":
            # working_directory is a session-level concept, not in tool_input.
            # No entities extracted for Bash calls.
            return []

        case "Agent":
            subagent = tool_input.get("subagent_type", "unknown")
            return [ExtractedEntity(kind="subagent", identifier=subagent, edge_type="called")]

        case _:
            return _extract_mcp(tool_name, tool_input)



def _file(path: str, edge_type: str) -> list[ExtractedEntity]:
    if not path:
        return []
    return [ExtractedEntity(kind="file", identifier=path, edge_type=edge_type)]


def _extract_mcp(tool_name: str, tool_input: dict[str, Any]) -> list[ExtractedEntity]:
    """Dispatch MCP tool names to provider-specific extractors."""
    if not tool_name.startswith("mcp__"):
        return []

    # mcp__memory__* — skip to avoid circular self-recording
    if tool_name.startswith("mcp__memory__"):
        return []

    if tool_name.startswith("mcp__github__"):
        return _github(tool_input)

    if tool_name.startswith("mcp__jira__"):
        return [ExtractedEntity(kind="api", identifier="jira", edge_type="called")]

    if tool_name.startswith("mcp__slack__"):
        return _slack(tool_input)

    if tool_name.startswith("mcp__aws__"):
        return [ExtractedEntity(kind="api", identifier="aws", edge_type="called")]

    # Unknown MCP provider — record the api entity with the provider name.
    provider = tool_name.split("__")[1] if tool_name.count("__") >= 2 else "unknown"
    return [ExtractedEntity(kind="api", identifier=provider, edge_type="called")]


def _github(tool_input: dict[str, Any]) -> list[ExtractedEntity]:
    entities: list[ExtractedEntity] = [
        ExtractedEntity(kind="api", identifier="github", edge_type="called"),
    ]

    # Prefer explicit owner + repo keys.
    owner = tool_input.get("owner")
    repo = tool_input.get("repo")
    if owner and repo:
        entities.append(
            ExtractedEntity(kind="repo", identifier=f"{owner}/{repo}", edge_type="targeted")
        )
        return entities

    # Fall back to a combined "repository" key in "owner/repo" format.
    repository = tool_input.get("repository", "")
    if repository and "/" in repository:
        entities.append(
            ExtractedEntity(kind="repo", identifier=repository, edge_type="targeted")
        )

    return entities


def _slack(tool_input: dict[str, Any]) -> list[ExtractedEntity]:
    entities: list[ExtractedEntity] = [
        ExtractedEntity(kind="api", identifier="slack", edge_type="called"),
    ]
    channel = tool_input.get("channel", "")
    if channel:
        entities.append(
            ExtractedEntity(kind="channel", identifier=channel, edge_type="called")
        )
    return entities
