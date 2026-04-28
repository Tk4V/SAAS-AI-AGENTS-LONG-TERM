"""BaseApiClient — the parent class for every `<name>/client.py`.

Owns three concerns so subclasses do not duplicate them:

1. Token resolution — fetches the access token via `TokenResolver` on every
   request. Resolution is per-request because the token may be refreshed
   between calls; we never cache the plaintext on the instance.

2. Bearer auth — adds `Authorization: Bearer <token>` automatically. If a
   provider requires a different scheme (Slack tokens go in the body for some
   endpoints, GitHub uses `Bearer` everywhere now), the subclass overrides
   `_auth_headers`.

3. URL composition — joins relative paths to `base_url`. Absolute URLs pass
   through untouched, which matters for endpoints that point at a different
   host (Atlassian's `accessible-resources`, Salesforce instance URLs).

Retries and rate-limit handling are intentionally *not* here yet. The retry
helper at `src/utils/retry.py` is per-provider and will be wired in once the
catalog grows past one entry. Premature centralization wastes review time.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.integrations._shared.exceptions import ProviderApiError, ProviderRateLimitError
from src.integrations._shared.kinds import IntegrationKind
from src.integrations._shared.token_resolver import TokenResolver


class BaseApiClient:
    def __init__(
        self,
        *,
        kind: IntegrationKind,
        user_id: int,
        token_resolver: TokenResolver,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._kind = kind
        self._user_id = user_id
        self._resolver = token_resolver
        self._base_url = base_url.rstrip("/") if base_url else None
        self._http = http_client or httpx.AsyncClient(timeout=30.0)
        self._owns_http = http_client is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _request(
        self,
        method: str,
        path_or_url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        token = await self._resolver.resolve(user_id=self._user_id, kind=self._kind)
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update(self._auth_headers(token))

        url = path_or_url if path_or_url.startswith("http") else self._build_url(path_or_url)
        response = await self._http.request(method, url, headers=headers, **kwargs)
        self._raise_for_status(response)
        return response

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _build_url(self, path: str) -> str:
        if not self._base_url:
            raise ProviderApiError(
                f"{self._kind.value}: relative path {path!r} given but no base_url is configured."
            )
        return f"{self._base_url}/{path.lstrip('/')}"

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        if response.status_code == 429:
            retry_after = self._parse_retry_after(response)
            raise ProviderRateLimitError(
                f"{self._kind.value}: rate limited.",
                retry_after=retry_after,
                status_code=429,
                body=response.text[:500],
            )
        raise ProviderApiError(
            f"{self._kind.value}: API call failed.",
            status_code=response.status_code,
            body=response.text[:500],
        )

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None
