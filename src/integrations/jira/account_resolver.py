"""Atlassian account discovery — resolves cloud_id and site_url after OAuth.

Called as ``OAuthProviderConfig.post_callback_hook`` by ``OAuthService.handle_callback``
immediately after the token exchange. The returned dict is merged into
``raw_metadata`` on the stored credential, making cloud_id and site_url
available to the Jira MCP factory without prompting the user.
"""

from __future__ import annotations

import httpx

from src.integrations._shared.exceptions import ProviderApiError

_ACCESSIBLE_RESOURCES_URL = (
    "https://api.atlassian.com/oauth/token/accessible-resources"
)


async def resolve_jira_account(access_token: str) -> dict[str, str]:
    """Call Atlassian's accessible-resources endpoint and return site metadata.

    Returns a dict with ``cloud_id`` and ``site_url`` for the first accessible
    Jira instance. Most users have exactly one cloud instance; if they have
    multiple we take the first one (alphabetically by name).

    Raises:
        ProviderApiError: if the HTTP request fails or returns no resources.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(_ACCESSIBLE_RESOURCES_URL, headers=headers)

    if response.status_code != 200:
        raise ProviderApiError(
            f"Atlassian accessible-resources returned {response.status_code}.",
            status_code=response.status_code,
            body=response.text,
        )

    resources: list[dict] = response.json()
    if not resources:
        raise ProviderApiError(
            "Atlassian accessible-resources returned an empty list. "
            "The token may lack the required scopes.",
            status_code=200,
            body="[]",
        )

    # Sort by name for deterministic selection when multiple sites exist.
    resources.sort(key=lambda r: r.get("name", ""))
    first = resources[0]

    cloud_id = first.get("id", "")
    site_url = first.get("url", "")

    if not cloud_id or not site_url:
        raise ProviderApiError(
            "Atlassian accessible-resources response is missing 'id' or 'url'.",
            status_code=200,
            body=str(first),
        )

    return {"cloud_id": cloud_id, "site_url": site_url}
