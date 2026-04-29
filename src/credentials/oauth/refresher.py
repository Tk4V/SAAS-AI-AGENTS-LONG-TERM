"""Auto-refresh logic for OAuth credentials.

Called transparently from ``CredentialResolver`` whenever an OAuth
credential is resolved. If the token is past its expiry (or close to it,
within a small clock-skew margin), we exchange the refresh token for a new
bundle and persist it before returning.

When the provider rejects the refresh — typical reasons are revocation in
the provider UI, password reset, or admin-level deactivation — the
credential is marked ``needs_reauth=True`` so the integrations UI can prompt
the user to reconnect, and the original error is re-raised so the calling
flow stops instead of operating on a stale token.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from src.credentials.kinds.registry import OAuthKindHandler
from src.credentials.payloads.oauth import OAuthMetadata, OAuthPayload, oauth_preview
from src.db.models.credential import Credential
from src.db.models.project import ProviderKind
from src.db.queries.credential_query import CredentialRepository
from src.integrations._shared import OAuthAdapter, ProviderRefreshError
from src.utils.crypto import TokenCipher

REFRESH_SKEW = timedelta(seconds=60)


class OAuthRefresher:
    def __init__(
        self,
        *,
        repository: CredentialRepository,
        adapter: OAuthAdapter,
        cipher: TokenCipher,
        handler: OAuthKindHandler,
    ) -> None:
        self._repo = repository
        self._adapter = adapter
        self._cipher = cipher
        self._handler = handler
        self._logger = structlog.get_logger("clyde.credentials.oauth.refresh")

    def needs_refresh(self, metadata: OAuthMetadata) -> bool:
        if metadata.needs_reauth:
            return False
        if metadata.expires_at is None:
            return False
        threshold = datetime.now(UTC) + REFRESH_SKEW
        return metadata.expires_at <= threshold

    async def refresh(
        self,
        *,
        credential: Credential,
        payload: OAuthPayload,
        metadata: OAuthMetadata,
    ) -> tuple[OAuthPayload, OAuthMetadata]:
        if payload.refresh_token is None:
            self._mark_needs_reauth(credential, metadata)
            return payload, metadata

        provider = ProviderKind(metadata.provider)
        try:
            bundle = await self._adapter.refresh(
                kind=provider, refresh_token=payload.refresh_token
            )
        except ProviderRefreshError as exc:
            self._logger.warning(
                "credentials.oauth.refresh.failed",
                credential_id=str(credential.id),
                provider=metadata.provider,
                error=str(exc),
            )
            self._mark_needs_reauth(credential, metadata)
            raise

        new_payload = OAuthPayload(
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token or payload.refresh_token,
        )
        new_metadata = OAuthMetadata(
            provider=metadata.provider,
            scopes=bundle.scopes or metadata.scopes,
            expires_at=bundle.expires_at,
            needs_reauth=False,
            raw={**metadata.raw, **dict(bundle.raw)},
        )

        credential.encrypted_payload = self._cipher.encrypt(
            self._handler.serialise_payload(new_payload)
        )
        credential.metadata_json = self._handler.serialise_metadata(new_metadata)
        credential.preview = oauth_preview(new_metadata.provider, new_metadata.scopes)
        self._logger.info(
            "credentials.oauth.refresh.success",
            credential_id=str(credential.id),
            provider=metadata.provider,
        )
        return new_payload, new_metadata

    def _mark_needs_reauth(
        self,
        credential: Credential,
        metadata: OAuthMetadata,
    ) -> None:
        flagged = OAuthMetadata(
            provider=metadata.provider,
            scopes=metadata.scopes,
            expires_at=metadata.expires_at,
            needs_reauth=True,
            raw=metadata.raw,
        )
        credential.metadata_json = self._handler.serialise_metadata(flagged)
