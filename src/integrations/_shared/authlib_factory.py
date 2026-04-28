"""Builds and caches one `AsyncOAuth2Client` per provider.

Authlib's client is the workhorse: it generates authorize URLs, performs the
PKCE-aware code exchange, refreshes tokens, and handles the various token
endpoint auth methods. We instantiate it lazily per `IntegrationKind` and
keep it for the rest of the process — building the client is cheap, but the
underlying `httpx.AsyncClient` connection pool is not.

Compliance hooks (Slack `ok:false`, GitHub `text/plain` token response, etc.)
are registered on the client immediately after construction, so every call
made through the cached instance is already corrected.
"""

from __future__ import annotations

from authlib.integrations.httpx_client import AsyncOAuth2Client

from src.config import Settings, get_settings
from src.integrations._shared.config import OAuthProviderConfig
from src.integrations._shared.exceptions import ProviderConfigError
from src.integrations._shared.kinds import IntegrationKind


class AuthlibClientFactory:
    """Lazy cache of `AsyncOAuth2Client` instances keyed by `IntegrationKind`.

    Tests should construct their own factory with a mock `Settings`. Production
    code receives one via `Clients` so the connection pools are shared.
    """

    def __init__(
        self,
        *,
        catalog_lookup: "ProviderLookup",
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._lookup = catalog_lookup
        self._cache: dict[IntegrationKind, AsyncOAuth2Client] = {}

    def get(self, kind: IntegrationKind) -> AsyncOAuth2Client:
        if kind not in self._cache:
            self._cache[kind] = self._build(self._lookup(kind))
        return self._cache[kind]

    def _build(self, config: OAuthProviderConfig) -> AsyncOAuth2Client:
        client_id = self._read_secret(config.client_id_setting, config.kind)
        client_secret = self._read_secret(config.client_secret_setting, config.kind)

        scope = config.scope_separator.join(config.default_scopes) or None
        client = AsyncOAuth2Client(
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
            token_endpoint=config.token_url,
            token_endpoint_auth_method=config.token_endpoint_auth_method,
            code_challenge_method=config.pkce_method if config.use_pkce else None,
        )

        if config.compliance_installer is not None:
            config.compliance_installer(client)

        return client

    def _read_secret(self, attr_name: str, kind: IntegrationKind) -> str:
        secret = getattr(self._settings, attr_name, None)
        if secret is None:
            raise ProviderConfigError(
                f"{kind.value}: settings has no field {attr_name!r}."
            )
        # Pydantic SecretStr exposes plaintext via `get_secret_value()`.
        getter = getattr(secret, "get_secret_value", None)
        value = getter() if callable(getter) else secret
        if not value:
            raise ProviderConfigError(
                f"{kind.value}: {attr_name} is empty. "
                f"Set it in .env or AWS Secrets Manager before connecting."
            )
        return str(value)

    async def aclose(self) -> None:
        for client in self._cache.values():
            await client.aclose()
        self._cache.clear()


# Type alias defined at module bottom to avoid forward-ref issues with the
# frozen import graph. `ProviderCatalog.get` matches this signature.
from collections.abc import Callable  # noqa: E402

ProviderLookup = Callable[[IntegrationKind], OAuthProviderConfig]
