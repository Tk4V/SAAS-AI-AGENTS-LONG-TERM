"""Public catalog of known providers for the integrations UI.

Frontend reads this catalog to render the "Connect a service" page: which
providers we know about, which auth methods each one supports, where to
open the deep-link for token creation, and what the docs URL is.

The catalog has no runtime side effects — it is a static, declarative list.
Providers that exist in ``src.integrations._shared.ProviderCatalog`` (i.e.
ones that already have an OAuth config) are cross-linked so the frontend
can call the right authorize endpoint without knowing the wiring.
"""

from src.credentials.catalog.models import (
    AuthMethod,
    AuthMethodKind,
    ProviderCatalogEntry,
    ProviderCategory,
)
from src.credentials.catalog.registry import (
    PROVIDER_CATALOG,
    PublicProviderCatalog,
    get_public_provider_catalog,
)

__all__ = [
    "PROVIDER_CATALOG",
    "AuthMethod",
    "AuthMethodKind",
    "ProviderCatalogEntry",
    "ProviderCategory",
    "PublicProviderCatalog",
    "get_public_provider_catalog",
]
