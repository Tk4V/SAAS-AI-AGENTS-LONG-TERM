"""Enum of every integration provider the system knows about.

`IntegrationKind` is an alias for the canonical DB-backed `ProviderKind`
enum (`src.db.models.project.ProviderKind`). Aliasing keeps a single
source of truth: the DB column type and the Python enum class are the same
object, so passing an `IntegrationKind` to a SQLAlchemy mapped attribute
just works.

`IntegrationCategory` groups providers for UI rendering and category-level
queries. It carries no behavior and is not persisted.
"""

from __future__ import annotations

import enum

from src.db.models.project import ProviderKind as IntegrationKind

__all__ = ["IntegrationCategory", "IntegrationKind"]


class IntegrationCategory(str, enum.Enum):
    CHAT = "chat"
    TRACKING = "tracking"
    MONITORING = "monitoring"
    VCS = "vcs"
    CRM = "crm"
    IDENTITY = "identity"
