"""OAuthAdapter — the one class every provider goes through.

What lives here:
- Building the authorize URL (with state + PKCE verifier embedded in JWT).
- Exchanging the callback code for a token.
- Refreshing the token.
- Revoking the token at the provider (when the provider supports RFC 7009).
- Fetching basic account info, when the provider's config declares a
  `userinfo_url`.

What does *not* live here:
- Persisting tokens to the DB — that's `OAuthCredentialService` (the orchestrator).
- Decrypting tokens for use — that's `OAuthTokenProvider`.
- Calling provider business APIs — that's `<name>/client.py`.

The adapter is intentionally provider-agnostic. Every quirk should be
expressed as either a config field or a `compliance_installer` hook so the
adapter itself stays a single, well-tested code path.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from src.integrations._shared.authlib_factory import AuthlibClientFactory
from src.integrations._shared.config import OAuthProviderConfig
from src.integrations._shared.exceptions import (
    ProviderApiError,
    ProviderAuthError,
    ProviderRefreshError,
)
from src.integrations._shared.kinds import IntegrationKind
from src.integrations._shared.state import OAuthStateSigner
from src.integrations._shared.tokens import TokenBundle


@dataclass(frozen=True, slots=True)
class AuthorizeRequest:
    """Bundle of values the route returns to the frontend.

    `url` is what the browser must navigate to. `state` is what the callback
    will receive back and we will verify; we expose it so logs and tests can
    correlate without re-parsing the URL.
    """

    url: str
    state: str


@dataclass(frozen=True, slots=True)
class CallbackResult:
    """What the callback handler gives back to `OAuthService`.

    The service is responsible for encrypting and persisting `token`
    against `user_id` for `kind`.
    """

    user_id: int
    kind: IntegrationKind
    token: TokenBundle


class OAuthAdapter:
    def __init__(
        self,
        *,
        catalog_lookup: ConfigLookup,
        client_factory: AuthlibClientFactory,
        state_signer: OAuthStateSigner,
    ) -> None:
        self._lookup = catalog_lookup
        self._factory = client_factory
        self._signer = state_signer

    def build_authorize_request(
        self,
        *,
        kind: IntegrationKind,
        user_id: int,
        redirect_uri: str,
    ) -> AuthorizeRequest:
        config = self._lookup(kind)
        client = self._factory.get(kind)

        verifier = secrets.token_urlsafe(48) if config.use_pkce else None
        state = self._signer.sign(
            user_id=user_id, provider=kind.value, pkce_verifier=verifier
        )

        url, _ = client.create_authorization_url(
            config.authorize_url,
            redirect_uri=redirect_uri,
            state=state,
            code_verifier=verifier,
            **dict(config.extra_authorize_params),
        )
        return AuthorizeRequest(url=url, state=state)

    async def handle_callback(
        self,
        *,
        kind: IntegrationKind,
        code: str,
        state: str,
        redirect_uri: str,
    ) -> CallbackResult:
        claims = self._signer.verify(state)
        if claims.get("provider") != kind.value:
            raise ProviderAuthError(
                "OAuth state does not match the callback provider."
            )

        config = self._lookup(kind)
        client = self._factory.get(kind)
        verifier = claims.get("pkce_verifier")

        try:
            token_data = await client.fetch_token(
                config.token_url,
                code=code,
                redirect_uri=redirect_uri,
                code_verifier=verifier,
            )
        except Exception as exc:
            raise ProviderAuthError(
                f"{kind.value}: code-for-token exchange failed."
            ) from exc

        bundle = TokenBundle.from_authlib(
            token_data,
            default_scopes=config.default_scopes,
            scope_separator=config.scope_separator,
        )
        return CallbackResult(
            user_id=int(claims["user_id"]),
            kind=kind,
            token=bundle,
        )

    async def refresh(
        self, *, kind: IntegrationKind, refresh_token: str
    ) -> TokenBundle:
        config = self._lookup(kind)
        if not config.refresh_supported:
            raise ProviderRefreshError(
                f"{kind.value} does not issue refresh tokens."
            )
        client = self._factory.get(kind)
        try:
            token_data = await client.refresh_token(
                config.token_url, refresh_token=refresh_token
            )
        except Exception as exc:
            raise ProviderRefreshError(
                f"{kind.value}: refresh failed; user must reconnect."
            ) from exc

        return TokenBundle.from_authlib(
            token_data,
            default_scopes=config.default_scopes,
            scope_separator=config.scope_separator,
        )

    async def revoke(self, *, kind: IntegrationKind, access_token: str) -> None:
        """Revoke `access_token` at the provider.

        Dispatch order:
        1. If the provider's config declares `custom_revoker`, use it. This is
           how non-RFC-7009 providers (GitHub OAuth Apps) plug in.
        2. Otherwise, if `revoke_url` is set, call it via Authlib's
           RFC-7009-style revocation.
        3. Otherwise this is a no-op — the credential is still deleted from
           our DB; the token simply lingers at the provider until it expires.
        """
        config = self._lookup(kind)
        if config.custom_revoker is not None:
            try:
                await config.custom_revoker(access_token)
            except Exception as exc:
                raise ProviderApiError(
                    f"{kind.value}: custom revoker failed."
                ) from exc
            return
        if not config.revoke_url:
            return
        client = self._factory.get(kind)
        try:
            await client.revoke_token(config.revoke_url, token=access_token)
        except Exception as exc:
            raise ProviderApiError(
                f"{kind.value}: token revocation rejected."
            ) from exc


# Same alias as in authlib_factory; kept local so adapter is self-contained.
from collections.abc import Callable  # noqa: E402

ConfigLookup = Callable[[IntegrationKind], OAuthProviderConfig]
