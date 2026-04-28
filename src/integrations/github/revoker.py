"""GitHub-specific OAuth token revocation.

GitHub OAuth Apps do not implement RFC 7009. Instead, they expose a DELETE
endpoint at ``/applications/{client_id}/token`` that requires Basic auth with
``client_id:client_secret``. Standard Authlib revocation cannot speak this
dialect, so we wire a `custom_revoker` into ``GITHUB`` config and handle the
call here.

A 204 means the token was deleted. A 404 means GitHub already considers it
invalid; we treat both as success because the caller's intent is satisfied.
"""

from __future__ import annotations

import httpx

from src.config import get_settings
from src.integrations._shared.exceptions import ProviderApiError


async def revoke_github_token(access_token: str) -> None:
    settings = get_settings()
    client_id = settings.github_oauth_client_id.get_secret_value()
    client_secret = settings.github_oauth_client_secret.get_secret_value()
    url = f"{settings.github_api_base}/applications/{client_id}/token"

    async with httpx.AsyncClient(timeout=30.0) as http:
        response = await http.request(
            "DELETE",
            url,
            json={"access_token": access_token},
            auth=(client_id, client_secret),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    if response.status_code not in (204, 404):
        raise ProviderApiError(
            "GitHub refused to revoke the OAuth token.",
            status_code=response.status_code,
            body=response.text[:500],
        )
