"""Registry of credential kinds.

Each kind is described once in ``registry.py`` and resolved by callers
through the ``KindRegistry`` rather than importing concrete payload modules
directly. This keeps the rest of the domain agnostic of which kinds happen
to be enabled in the current build.
"""

from src.credentials.kinds.base import KindHandler
from src.credentials.kinds.registry import KindRegistry, get_kind_registry

__all__ = ["KindHandler", "KindRegistry", "get_kind_registry"]
