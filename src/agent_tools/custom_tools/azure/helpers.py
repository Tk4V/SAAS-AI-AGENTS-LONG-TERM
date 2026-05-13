"""HTTP helpers for the Azure skill server.

Lower-level ``httpx`` calls used by the tool classes in ``tools.py``.
Kept separate so the tool layer stays declarative and the HTTP layer can
be tested in isolation.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.config.settings import get_settings

DEFAULT_TIMEOUT_SEC = 30
DEFAULT_TAIL_LINES_PER_OPERATION = 50
_API_VERSION = "2021-04-01"


async def fetch_subscriptions(
    *,
    token: str,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    """Return all subscriptions the token can see.

    Each item contains at least ``subscriptionId``, ``displayName``, and
    ``state``. Returns an empty list on error, surfacing the reason as a
    single item with an ``error`` key.
    """
    api_base = get_settings().azure_management_api_base
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{api_base}/subscriptions?api-version={_API_VERSION}"

    async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return [{"error": f"Failed to list subscriptions: {exc}"}]
        return response.json().get("value", [])


async def fetch_resource_groups(
    *,
    token: str,
    subscription_id: str,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    """Return all resource groups in a subscription.

    Each item contains at least ``name``, ``location``, and
    ``properties.provisioningState``. Returns a list with a single error
    item if the request fails.
    """
    api_base = get_settings().azure_management_api_base
    headers = {"Authorization": f"Bearer {token}"}
    url = (
        f"{api_base}/subscriptions/{subscription_id}"
        f"/resourcegroups?api-version={_API_VERSION}"
    )

    async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return [{"error": f"Failed to list resource groups: {exc}"}]
        return response.json().get("value", [])


async def fetch_virtual_machines(
    *,
    token: str,
    subscription_id: str,
    resource_group: str | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    """Return VMs in a subscription, optionally filtered to one resource group.

    Each item contains at least ``name``, ``location``, ``id``, and
    ``properties``. Returns a list with a single error item on failure.
    """
    api_base = get_settings().azure_management_api_base
    headers = {"Authorization": f"Bearer {token}"}

    if resource_group:
        url = (
            f"{api_base}/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Compute/virtualMachines"
            f"?api-version=2024-03-01"
        )
    else:
        url = (
            f"{api_base}/subscriptions/{subscription_id}"
            f"/providers/Microsoft.Compute/virtualMachines"
            f"?api-version=2024-03-01"
        )

    async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return [{"error": f"Failed to list virtual machines: {exc}"}]
        return response.json().get("value", [])


async def fetch_failed_deployment_logs(
    *,
    token: str,
    subscription_id: str,
    resource_group: str,
    deployment_name: str,
    max_operations: int = DEFAULT_TAIL_LINES_PER_OPERATION,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> str:
    """Return error details from a failed ARM deployment.

    Fetches the deployment operations list and returns the status messages
    from every failed operation, trimmed to ``max_operations`` entries.
    Errors at any step are surfaced as diagnostic text.
    """
    api_base = get_settings().azure_management_api_base
    headers = {"Authorization": f"Bearer {token}"}
    ops_url = (
        f"{api_base}/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Resources/deployments/{deployment_name}"
        f"/operations?api-version={_API_VERSION}"
    )

    async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
        try:
            ops_response = await client.get(ops_url, headers=headers)
            ops_response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"(failed to fetch deployment operations for '{deployment_name}': {exc})"

        operations = ops_response.json().get("value", [])
        failed_ops = [
            op
            for op in operations
            if op.get("properties", {}).get("provisioningState") == "Failed"
        ]

        if not failed_ops:
            return (
                f"(no failed operations found for deployment '{deployment_name}'; "
                f"the deployment may still be in progress or failed at the submission stage)"
            )

        sections = []
        for op in failed_ops[:max_operations]:
            props = op.get("properties", {})
            resource_type = props.get("targetResource", {}).get("resourceType", "unknown")
            resource_name = props.get("targetResource", {}).get("resourceName", "unknown")
            status_code = props.get("statusCode", "unknown")
            status_message = props.get("statusMessage", {})
            error = status_message.get("error", status_message) if isinstance(status_message, dict) else status_message
            sections.append(
                f"### Operation: {resource_type}/{resource_name}\n"
                f"Status: {status_code}\n"
                f"Error: {error}"
            )

    return "\n\n".join(sections)
