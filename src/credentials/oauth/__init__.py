"""OAuth flow inside the credentials domain.

Reuses the provider-agnostic machinery from ``src.integrations._shared``
(``OAuthAdapter``, ``OAuthStateSigner``, ``AuthlibClientFactory``,
``ProviderCatalog``) and persists the resulting tokens into the unified
``credentials`` table as ``CredentialKind.OAUTH``. The legacy
``src.services.oauth_service`` continues to write into
``user_oauth_credentials`` until step 3 retires it.
"""

from src.credentials.oauth.refresher import OAuthRefresher
from src.credentials.oauth.service import OAuthCredentialService

__all__ = [
    "OAuthCredentialService",
    "OAuthRefresher",
]
