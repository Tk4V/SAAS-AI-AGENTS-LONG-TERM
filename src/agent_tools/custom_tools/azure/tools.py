"""Skill-tool classes for the Azure in-process MCP server.

Each class subclasses ``BaseSkillTool``, declares ``name``,
``description``, ``input_schema`` as class-level fields, captures
per-session service principal credentials in ``__init__``, and
implements ``run``. The server in ``server.py`` instantiates them and
hands the list to ``build_mcp_server``.
"""

from __future__ import annotations

import shlex
from typing import Any, ClassVar

from src.agent_tools.custom_tools.azure.helpers import connect_azure, run_az
from src.agent_tools.custom_tools.mcp_server_builder import BaseSkillTool


class ConnectAzureTool(BaseSkillTool):
    """Establishes an Azure CLI session via service principal login.

    Must be called before any other Azure tool in a new session. Uses the
    credentials stored at server-build time — no arguments required.
    """

    name: ClassVar[str] = "connect_azure"
    description: ClassVar[str] = (
        "Connect to Azure using the stored service principal credentials "
        "(client_id, client_secret, tenant_id, subscription_id). Call this "
        "first before any other Azure operation. Runs "
        "`az login --service-principal` and sets the active subscription. "
        "Returns a confirmation message or an error description."
    )
    input_schema: ClassVar[dict[str, Any]] = {}

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        text = await connect_azure(self._credentials)
        return {"content": [{"type": "text", "text": text}]}


class RunAzCommandTool(BaseSkillTool):
    """Runs an arbitrary ``az`` CLI command and returns its output.

    The primary way for the agent to interact with Azure after connecting.
    All standard ``az`` subcommands are available.
    """

    name: ClassVar[str] = "run_az_command"
    description: ClassVar[str] = (
        "Run any Azure CLI (`az`) command and return its output as JSON. "
        "Provide the arguments as a single string exactly as you would type "
        "after `az` on the command line "
        '(e.g. `"group list"`, `"vm show --name myVM --resource-group myRG"`, '
        '`"deployment group list --resource-group myRG"`). '
        "Use shell-style quoting for arguments that contain spaces. "
        "Output defaults to JSON unless you include `--output table` etc. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "command": str,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd_args = shlex.split(args["command"])
        text = await run_az(cmd_args, self._credentials)
        return {"content": [{"type": "text", "text": text}]}


class ListSubscriptionsTool(BaseSkillTool):
    """Lists all Azure subscriptions accessible with the stored credentials.

    Useful as the first discovery step when the subscription ID is unknown.
    """

    name: ClassVar[str] = "list_subscriptions"
    description: ClassVar[str] = (
        "List all Azure subscriptions accessible with the current credentials. "
        "Returns subscription ID, display name, and state for each subscription. "
        "Use this when you do not yet know the subscription ID. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {}

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        text = await run_az(["account", "list"], self._credentials)
        return {"content": [{"type": "text", "text": text}]}


class ListResourceGroupsTool(BaseSkillTool):
    """Lists resource groups in an Azure subscription.

    Returns name, location, and provisioning state for each group.
    """

    name: ClassVar[str] = "list_resource_groups"
    description: ClassVar[str] = (
        "List all resource groups in an Azure subscription. "
        "Returns name, location, and provisioningState for each group. "
        "Use this to discover available resource groups before operating on "
        "resources. Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "subscription_id": str,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        text = await run_az(
            ["group", "list", "--subscription", args["subscription_id"]],
            self._credentials,
        )
        return {"content": [{"type": "text", "text": text}]}


class ListVirtualMachinesTool(BaseSkillTool):
    """Lists virtual machines in a subscription, optionally within one resource group."""

    name: ClassVar[str] = "list_virtual_machines"
    description: ClassVar[str] = (
        "List virtual machines in an Azure subscription, optionally filtered "
        "to a specific resource group. Returns name, location, VM size, and "
        "power state for each VM. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "subscription_id": str,
        "resource_group": str | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = ["vm", "list", "--subscription", args["subscription_id"], "--show-details"]
        if args.get("resource_group"):
            cmd += ["--resource-group", args["resource_group"]]
        text = await run_az(cmd, self._credentials)
        return {"content": [{"type": "text", "text": text}]}
