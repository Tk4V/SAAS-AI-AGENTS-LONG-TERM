"""Skill-tool classes for the Azure in-process MCP server.

Each class subclasses ``BaseSkillTool``, declares ``name``,
``description``, ``input_schema`` as class-level fields, captures any
per-session credentials in ``__init__``, and implements ``run``. The
server in ``server.py`` instantiates them and hands the list to
``build_mcp_server``.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from src.agent_tools.custom_tools.azure.helpers import (
    fetch_failed_deployment_logs,
    fetch_resource_groups,
    fetch_subscriptions,
    fetch_virtual_machines,
)
from src.agent_tools.custom_tools.mcp_server_builder import BaseSkillTool


class ListSubscriptionsTool(BaseSkillTool):
    """Lists all Azure subscriptions the token can access.

    Use this first when the subscription ID is not known — it returns the
    subscription IDs, display names, and states needed to call other tools.
    """

    name: ClassVar[str] = "list_subscriptions"
    description: ClassVar[str] = (
        "List all Azure subscriptions accessible with the current credentials. "
        "Returns subscription ID, display name, and state for each subscription. "
        "Use this when you do not yet know the subscription ID."
    )
    input_schema: ClassVar[dict[str, Any]] = {}

    def __init__(self, azure_token: str) -> None:
        self._token = azure_token

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        subscriptions = await fetch_subscriptions(token=self._token)
        text = json.dumps(subscriptions, indent=2)
        return {"content": [{"type": "text", "text": text}]}


class ListResourceGroupsTool(BaseSkillTool):
    """Lists all resource groups in an Azure subscription.

    Returns the name, location, and provisioning state of each group.
    Use this to enumerate the resource groups before operating on resources.
    """

    name: ClassVar[str] = "list_resource_groups"
    description: ClassVar[str] = (
        "List all resource groups in an Azure subscription. "
        "Returns name, location, and provisioningState for each group. "
        "Use this to discover what resource groups exist before operating on resources."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "subscription_id": str,
    }

    def __init__(self, azure_token: str) -> None:
        self._token = azure_token

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        groups = await fetch_resource_groups(
            token=self._token,
            subscription_id=args["subscription_id"],
        )
        text = json.dumps(groups, indent=2)
        return {"content": [{"type": "text", "text": text}]}


class ListVirtualMachinesTool(BaseSkillTool):
    """Lists virtual machines in a subscription or resource group.

    Returns name, location, VM size, and OS profile for each VM.
    Optionally filter to a single resource group.
    """

    name: ClassVar[str] = "list_virtual_machines"
    description: ClassVar[str] = (
        "List virtual machines in an Azure subscription, optionally filtered "
        "to a specific resource group. Returns name, location, VM size, and "
        "OS information for each VM."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "subscription_id": str,
        "resource_group": str | None,
    }

    def __init__(self, azure_token: str) -> None:
        self._token = azure_token

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        vms = await fetch_virtual_machines(
            token=self._token,
            subscription_id=args["subscription_id"],
            resource_group=args.get("resource_group"),
        )
        text = json.dumps(vms, indent=2)
        return {"content": [{"type": "text", "text": text}]}


class GetFailedDeploymentLogsTool(BaseSkillTool):
    """Returns error details from every failed operation in an ARM deployment.

    Use this as the first step when diagnosing a failed Azure deployment —
    reads the actual error messages before any further investigation.
    """

    name: ClassVar[str] = "get_failed_deployment_logs"
    description: ClassVar[str] = (
        "Fetch error details from every failed operation in a Azure Resource "
        "Manager deployment. Returns a multi-section plain-text string with "
        "one section per failed operation, including the resource type, "
        "status code, and error message. Use this as the first step when "
        "diagnosing a failed ARM deployment."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "subscription_id": str,
        "resource_group": str,
        "deployment_name": str,
    }

    def __init__(self, azure_token: str) -> None:
        self._token = azure_token

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        text = await fetch_failed_deployment_logs(
            token=self._token,
            subscription_id=args["subscription_id"],
            resource_group=args["resource_group"],
            deployment_name=args["deployment_name"],
        )
        return {"content": [{"type": "text", "text": text}]}
