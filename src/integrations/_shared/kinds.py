"""Enum of every integration provider the system knows about.

`IntegrationKind` is an alias for the canonical DB-backed `GitProviderKind`
enum (`src.db.models.project.GitProviderKind`). Aliasing keeps a single
source of truth: the DB column type and the Python enum class are the same
object, so passing an `IntegrationKind` to a SQLAlchemy mapped attribute
just works.

The name change is forward-looking: as we add Slack/Discord/Jira (after the
DB enum is renamed in a separate migration), code that already imports
`IntegrationKind` from this module won't need to change.

`IntegrationCategory` groups providers for UI rendering and category-level
queries. It carries no behavior and is not persisted.
"""

from __future__ import annotations

import enum

from src.db.models.project import GitProviderKind as IntegrationKind

__all__ = ["IntegrationCategory", "IntegrationKind"]


class IntegrationCategory(str, enum.Enum):
    CHAT = "chat"
    TRACKING = "tracking"
    MONITORING = "monitoring"
    VCS = "vcs"
    CRM = "crm"
    IDENTITY = "identity"
