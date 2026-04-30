"""OAuth flow inside the credentials domain.

Reuses the provider-agnostic machinery from ``src.integrations._shared``
(``OAuthAdapter``, ``OAuthStateSigner``, ``AuthlibClientFactory``,
``ProviderCatalog``) and persists the resulting tokens into the unified
``credentials`` table as ``CredentialKind.OAUTH``.
"""

from src.credentials.oauth.refresher import OAuthRefresher
from src.credentials.oauth.service import OAuthCredentialService

# OAuthTokenProvider is intentionally not re-exported here to avoid a circular
# import: it depends on CredentialResolver which depends on OAuthRefresher,
# and importing it from this package init would close the cycle. Import it
# explicitly from ``credentials.oauth.token_provider`` where needed.

__all__ = [
    "OAuthCredentialService",
    "OAuthRefresher",
]
