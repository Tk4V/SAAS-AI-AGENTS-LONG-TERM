"""Public surface of the shared integration framework.

Anything a provider folder or service needs from `_shared/` should import it
from this module, not from the underlying file. That gives us one chokepoint
to refactor internal layout without breaking callers.
"""

from src.integrations._shared.adapter import (
    AuthorizeRequest,
    CallbackResult,
    OAuthAdapter,
)
from src.integrations._shared.api_base import BaseApiClient
from src.integrations._shared.authlib_factory import AuthlibClientFactory
from src.integrations._shared.config import (
    ComplianceInstaller,
    MCPFactory,
    OAuthProviderConfig,
    TokenRevoker,
)
from src.integrations._shared.exceptions import (
    ProviderApiError,
    ProviderAuthError,
    ProviderConfigError,
    ProviderError,
    ProviderRateLimitError,
    ProviderRefreshError,
)
from src.integrations._shared.kinds import IntegrationCategory, IntegrationKind
from src.integrations._shared.registry import ProviderCatalog
from src.integrations._shared.state import OAuthStateSigner
from src.integrations._shared.token_resolver import TokenResolver
from src.integrations._shared.tokens import TokenBundle

__all__ = [
    "AuthlibClientFactory",
    "AuthorizeRequest",
    "BaseApiClient",
    "CallbackResult",
    "ComplianceInstaller",
    "MCPFactory",
    "IntegrationCategory",
    "IntegrationKind",
    "OAuthAdapter",
    "OAuthProviderConfig",
    "OAuthStateSigner",
    "ProviderApiError",
    "ProviderAuthError",
    "ProviderCatalog",
    "ProviderConfigError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderRefreshError",
    "TokenBundle",
    "TokenResolver",
    "TokenRevoker",
]
