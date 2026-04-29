"""OAuth orchestration that writes into the unified credentials table.

Delegates the protocol-level work (authorize URL, code exchange) to the
shared ``OAuthAdapter`` and only knows how to persist the result as a
``CredentialKind.OAUTH`` row in ``credentials``. Each connection becomes a
new credential row; reconnecting the same provider creates a new one rather
than mutating the previous, so the audit log carries an unambiguous link
between any access and the exact token row it used.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from src.config import Settings, get_settings
from src.credentials.audit import CredentialAuditor
from src.credentials.kinds.registry import OAuthKindHandler
from src.credentials.payloads.oauth import (
    OAuthMetadata,
    OAuthPayload,
    oauth_preview,
)
from src.db.models.credential import Credential, CredentialKind
from src.db.models.project import ProviderKind
from src.db.queries.credential_query import CredentialRepository
from src.integrations._shared import (
    OAuthAdapter,
    ProviderApiError,
    ProviderAuthError,
    ProviderCatalog,
)
from src.utils.crypto import TokenCipher
from src.utils.exceptions import AuthenticationError


class OAuthCredentialService:
    def __init__(
        self,
        *,
        repository: CredentialRepository,
        adapter: OAuthAdapter,
        catalog: ProviderCatalog,
        cipher: TokenCipher,
        auditor: CredentialAuditor,
        handler: OAuthKindHandler,
        settings: Settings | None = None,
    ) -> None:
        self._repo = repository
        self._adapter = adapter
        self._catalog = catalog
        self._cipher = cipher
        self._auditor = auditor
        self._handler = handler
        self._settings = settings or get_settings()
        self._logger = structlog.get_logger("clyde.credentials.oauth")

    def start_flow(
        self,
        *,
        user_id: int,
        provider: ProviderKind,
    ) -> str:
        request = self._adapter.build_authorize_request(
            kind=provider,
            user_id=user_id,
            redirect_uri=self._callback_url(provider),
        )
        self._logger.info(
            "credentials.oauth.start",
            user_id=user_id,
            provider=provider.value,
        )
        return request.url

    async def handle_callback(
        self,
        *,
        provider: ProviderKind,
        code: str,
        state: str,
    ) -> Credential:
        try:
            result = await self._adapter.handle_callback(
                kind=provider,
                code=code,
                state=state,
                redirect_uri=self._callback_url(provider),
            )
        except ProviderAuthError as exc:
            raise AuthenticationError(str(exc)) from exc

        bundle = result.token
        raw_metadata = dict(bundle.raw)

        cfg = self._catalog.get(provider)
        if cfg.post_callback_hook is not None:
            try:
                extra = await cfg.post_callback_hook(bundle.access_token)
                raw_metadata.update(extra)
            except Exception as exc:
                self._logger.warning(
                    "credentials.oauth.post_callback_hook.failed",
                    user_id=result.user_id,
                    provider=provider.value,
                    error=str(exc),
                )

        payload = OAuthPayload(
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token,
        )
        metadata = OAuthMetadata(
            provider=provider.value,
            scopes=bundle.scopes,
            expires_at=bundle.expires_at,
            needs_reauth=False,
            raw=raw_metadata,
        )

        encrypted = self._cipher.encrypt(self._handler.serialise_payload(payload))
        preview = oauth_preview(provider.value, bundle.scopes)
        label = f"{cfg.display_name} ({result.user_id})"

        credential = await self._repo.create(
            user_id=result.user_id,
            kind=CredentialKind.OAUTH,
            label=label,
            encrypted_payload=encrypted,
            preview=preview,
            metadata=self._handler.serialise_metadata(metadata),
        )
        await self._auditor.created(
            user_id=result.user_id,
            credential_id=credential.id,
            details={"kind": CredentialKind.OAUTH.value, "provider": provider.value},
        )
        self._logger.info(
            "credentials.oauth.callback.success",
            user_id=result.user_id,
            provider=provider.value,
            credential_id=str(credential.id),
        )
        return credential

    async def revoke(
        self,
        *,
        user_id: int,
        credential_id: UUID,
    ) -> None:
        credential = await self._repo.get(
            user_id=user_id, credential_id=credential_id
        )
        provider = ProviderKind(credential.metadata_json.get("provider"))
        plaintext = self._cipher.decrypt(credential.encrypted_payload)
        payload = self._handler.deserialise_payload(plaintext)
        try:
            await self._adapter.revoke(
                kind=provider, access_token=payload.access_token
            )
        except ProviderApiError as exc:
            self._logger.warning(
                "credentials.oauth.revoke.remote_failed",
                user_id=user_id,
                provider=provider.value,
                error=str(exc),
            )
        await self._repo.soft_delete(
            user_id=user_id, credential_id=credential_id
        )
        await self._auditor.deleted(
            user_id=user_id,
            credential_id=credential.id,
            details={"kind": CredentialKind.OAUTH.value, "provider": provider.value},
        )

    def _callback_url(self, provider: ProviderKind) -> str:
        return (
            f"{self._settings.oauth_callback_base_url.rstrip('/')}"
            f"{self._settings.api_prefix}"
            f"/credentials/oauth/{provider.value}/callback"
        )

    def build_callback_redirect_url(
        self,
        *,
        provider: ProviderKind,
        success: bool,
        error_code: str | None = None,
    ) -> str:
        base = self._settings.frontend_redirect_url.rstrip("?&")
        separator = "&" if "?" in base else "?"
        if success:
            return f"{base}{separator}integration={provider.value}&status=ok"
        return (
            f"{base}{separator}integration={provider.value}"
            f"&status=error&code={error_code or 'unknown'}"
        )
