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


# ---------------------------------------------------------------------------
# Discovery / read tools
# ---------------------------------------------------------------------------


class ListSubscriptionsTool(BaseSkillTool):
    """Lists all Azure subscriptions accessible with the stored credentials."""

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
    """Lists resource groups in an Azure subscription."""

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


# ---------------------------------------------------------------------------
# Resource group management
# ---------------------------------------------------------------------------


class CreateResourceGroupTool(BaseSkillTool):
    """Creates a new Azure resource group."""

    name: ClassVar[str] = "create_resource_group"
    description: ClassVar[str] = (
        "Create a new Azure resource group in the given subscription. "
        "Provide a unique name, an Azure region (e.g. 'eastus', 'westeurope'), "
        "and the subscription ID. Returns the created resource group details. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "location": str,
        "subscription_id": str,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        text = await run_az(
            [
                "group", "create",
                "--name", args["name"],
                "--location", args["location"],
                "--subscription", args["subscription_id"],
            ],
            self._credentials,
        )
        return {"content": [{"type": "text", "text": text}]}


class DeleteResourceGroupTool(BaseSkillTool):
    """Deletes an Azure resource group and all its resources."""

    name: ClassVar[str] = "delete_resource_group"
    description: ClassVar[str] = (
        "Delete an Azure resource group and ALL resources inside it. "
        "This is irreversible. "
        "Without confirmed=true this tool performs a dry run and returns a summary "
        "of what would be deleted — no changes are made. "
        "You MUST call `ask_user` first to get explicit user approval, "
        "then re-call this tool with confirmed=true to execute. "
        "The deletion runs asynchronously; use `run_az_command` with "
        "`group show` to poll for completion if needed. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "subscription_id": str,
        "confirmed": bool | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("confirmed"):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "DRY RUN — no changes made.\n"
                        f"This would permanently delete resource group '{args['name']}' "
                        f"(subscription: {args['subscription_id']}) and ALL resources inside it.\n"
                        "To proceed: call `ask_user` to get explicit user approval, "
                        "then re-call this tool with confirmed=true."
                    ),
                }]
            }
        text = await run_az(
            [
                "group", "delete",
                "--name", args["name"],
                "--subscription", args["subscription_id"],
                "--yes", "--no-wait",
            ],
            self._credentials,
            timeout_sec=120,
        )
        return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------------------
# Virtual machine management
# ---------------------------------------------------------------------------


class GetVirtualMachineTool(BaseSkillTool):
    """Gets details for a specific virtual machine."""

    name: ClassVar[str] = "get_virtual_machine"
    description: ClassVar[str] = (
        "Get detailed information about a specific Azure VM including its "
        "power state, OS disk, network interfaces, and tags. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "subscription_id": str,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        text = await run_az(
            [
                "vm", "show",
                "--name", args["name"],
                "--resource-group", args["resource_group"],
                "--subscription", args["subscription_id"],
                "--show-details",
            ],
            self._credentials,
        )
        return {"content": [{"type": "text", "text": text}]}


class CreateVirtualMachineTool(BaseSkillTool):
    """Creates a new Azure virtual machine."""

    name: ClassVar[str] = "create_virtual_machine"
    description: ClassVar[str] = (
        "Create a new Azure virtual machine. Required: name, resource_group, "
        "location, image (e.g. 'Ubuntu2204', 'Win2022Datacenter'), size "
        "(e.g. 'Standard_B2s'), admin_username, subscription_id. "
        "Optional: admin_password (for password auth) or ssh_key_value (public key "
        "string for Linux key-based auth). If neither is provided, Azure generates "
        "SSH keys. Returns the created VM details. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "location": str,
        "image": str,
        "size": str,
        "admin_username": str,
        "subscription_id": str,
        "admin_password": str | None,
        "ssh_key_value": str | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = [
            "vm", "create",
            "--name", args["name"],
            "--resource-group", args["resource_group"],
            "--location", args["location"],
            "--image", args["image"],
            "--size", args["size"],
            "--admin-username", args["admin_username"],
            "--subscription", args["subscription_id"],
        ]
        if args.get("admin_password"):
            cmd += ["--admin-password", args["admin_password"]]
        elif args.get("ssh_key_value"):
            cmd += ["--ssh-key-value", args["ssh_key_value"]]
        else:
            cmd += ["--generate-ssh-keys"]
        text = await run_az(cmd, self._credentials, timeout_sec=300)
        return {"content": [{"type": "text", "text": text}]}


class StartVirtualMachineTool(BaseSkillTool):
    """Starts a stopped or deallocated Azure virtual machine."""

    name: ClassVar[str] = "start_virtual_machine"
    description: ClassVar[str] = (
        "Start a stopped or deallocated Azure VM. "
        "Provide the VM name, resource group, and subscription ID. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "subscription_id": str,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        text = await run_az(
            [
                "vm", "start",
                "--name", args["name"],
                "--resource-group", args["resource_group"],
                "--subscription", args["subscription_id"],
            ],
            self._credentials,
            timeout_sec=120,
        )
        return {"content": [{"type": "text", "text": text}]}


class StopVirtualMachineTool(BaseSkillTool):
    """Stops (deallocates) an Azure virtual machine."""

    name: ClassVar[str] = "stop_virtual_machine"
    description: ClassVar[str] = (
        "Stop and deallocate an Azure VM (billing for compute stops). "
        "Provide the VM name, resource group, and subscription ID. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "subscription_id": str,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        text = await run_az(
            [
                "vm", "deallocate",
                "--name", args["name"],
                "--resource-group", args["resource_group"],
                "--subscription", args["subscription_id"],
            ],
            self._credentials,
            timeout_sec=120,
        )
        return {"content": [{"type": "text", "text": text}]}


class RestartVirtualMachineTool(BaseSkillTool):
    """Restarts an Azure virtual machine."""

    name: ClassVar[str] = "restart_virtual_machine"
    description: ClassVar[str] = (
        "Restart a running Azure VM. "
        "Provide the VM name, resource group, and subscription ID. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "subscription_id": str,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        text = await run_az(
            [
                "vm", "restart",
                "--name", args["name"],
                "--resource-group", args["resource_group"],
                "--subscription", args["subscription_id"],
            ],
            self._credentials,
            timeout_sec=120,
        )
        return {"content": [{"type": "text", "text": text}]}


class DeleteVirtualMachineTool(BaseSkillTool):
    """Deletes an Azure virtual machine."""

    name: ClassVar[str] = "delete_virtual_machine"
    description: ClassVar[str] = (
        "Delete an Azure VM. Optionally delete associated OS disk by setting "
        "delete_os_disk=true. "
        "Without confirmed=true this tool performs a dry run and returns a summary "
        "of what would be deleted — no changes are made. "
        "You MUST call `ask_user` first to get explicit user approval, "
        "then re-call this tool with confirmed=true to execute. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "subscription_id": str,
        "confirmed": bool | None,
        "delete_os_disk": bool | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("confirmed"):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "DRY RUN — no changes made.\n"
                        f"This would permanently delete VM '{args['name']}' "
                        f"in resource group '{args['resource_group']}' "
                        f"(subscription: {args['subscription_id']})."
                        + (" OS disk would also be deleted." if args.get("delete_os_disk") else "")
                        + "\nTo proceed: call `ask_user` to get explicit user approval, "
                        "then re-call this tool with confirmed=true."
                    ),
                }]
            }
        cmd = [
            "vm", "delete",
            "--name", args["name"],
            "--resource-group", args["resource_group"],
            "--subscription", args["subscription_id"],
            "--yes",
        ]
        if args.get("delete_os_disk"):
            cmd += ["--force-deletion", "true"]
        text = await run_az(cmd, self._credentials, timeout_sec=120)
        return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------------------
# Storage account management
# ---------------------------------------------------------------------------


class ListStorageAccountsTool(BaseSkillTool):
    """Lists Azure storage accounts in a subscription or resource group."""

    name: ClassVar[str] = "list_storage_accounts"
    description: ClassVar[str] = (
        "List Azure storage accounts. Optionally filter by resource group. "
        "Returns name, location, SKU, and kind for each account. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "subscription_id": str,
        "resource_group": str | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = ["storage", "account", "list", "--subscription", args["subscription_id"]]
        if args.get("resource_group"):
            cmd += ["--resource-group", args["resource_group"]]
        text = await run_az(cmd, self._credentials)
        return {"content": [{"type": "text", "text": text}]}


class CreateStorageAccountTool(BaseSkillTool):
    """Creates a new Azure storage account."""

    name: ClassVar[str] = "create_storage_account"
    description: ClassVar[str] = (
        "Create a new Azure storage account. Name must be 3-24 lowercase alphanumeric "
        "characters and globally unique. SKU options: 'Standard_LRS' (default), "
        "'Standard_GRS', 'Standard_ZRS', 'Premium_LRS'. "
        "Kind options: 'StorageV2' (default), 'BlobStorage', 'FileStorage'. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "location": str,
        "subscription_id": str,
        "sku": str | None,
        "kind": str | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = [
            "storage", "account", "create",
            "--name", args["name"],
            "--resource-group", args["resource_group"],
            "--location", args["location"],
            "--subscription", args["subscription_id"],
            "--sku", args.get("sku") or "Standard_LRS",
            "--kind", args.get("kind") or "StorageV2",
        ]
        text = await run_az(cmd, self._credentials, timeout_sec=120)
        return {"content": [{"type": "text", "text": text}]}


class DeleteStorageAccountTool(BaseSkillTool):
    """Deletes an Azure storage account."""

    name: ClassVar[str] = "delete_storage_account"
    description: ClassVar[str] = (
        "Delete an Azure storage account and all its data. This is irreversible. "
        "Without confirmed=true this tool performs a dry run and returns a summary "
        "of what would be deleted — no changes are made. "
        "You MUST call `ask_user` first to get explicit user approval, "
        "then re-call this tool with confirmed=true to execute. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "subscription_id": str,
        "confirmed": bool | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("confirmed"):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "DRY RUN — no changes made.\n"
                        f"This would permanently delete storage account '{args['name']}' "
                        f"in resource group '{args['resource_group']}' "
                        f"(subscription: {args['subscription_id']}) and ALL data inside it.\n"
                        "To proceed: call `ask_user` to get explicit user approval, "
                        "then re-call this tool with confirmed=true."
                    ),
                }]
            }
        text = await run_az(
            [
                "storage", "account", "delete",
                "--name", args["name"],
                "--resource-group", args["resource_group"],
                "--subscription", args["subscription_id"],
                "--yes",
            ],
            self._credentials,
        )
        return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------------------
# App Service / Web App management
# ---------------------------------------------------------------------------


class ListWebAppsTool(BaseSkillTool):
    """Lists Azure App Service web apps."""

    name: ClassVar[str] = "list_web_apps"
    description: ClassVar[str] = (
        "List Azure App Service web apps in a subscription, optionally filtered "
        "by resource group. Returns name, location, state, and default hostname. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "subscription_id": str,
        "resource_group": str | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = ["webapp", "list", "--subscription", args["subscription_id"]]
        if args.get("resource_group"):
            cmd += ["--resource-group", args["resource_group"]]
        text = await run_az(cmd, self._credentials)
        return {"content": [{"type": "text", "text": text}]}


class CreateAppServicePlanTool(BaseSkillTool):
    """Creates an Azure App Service plan."""

    name: ClassVar[str] = "create_app_service_plan"
    description: ClassVar[str] = (
        "Create an Azure App Service plan which hosts web apps. "
        "SKU options: 'F1' (Free), 'B1'/'B2'/'B3' (Basic), 'S1'/'S2'/'S3' (Standard), "
        "'P1v3'/'P2v3'/'P3v3' (Premium v3). Use '--is-linux' for Linux plans. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "location": str,
        "subscription_id": str,
        "sku": str | None,
        "is_linux": bool | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = [
            "appservice", "plan", "create",
            "--name", args["name"],
            "--resource-group", args["resource_group"],
            "--location", args["location"],
            "--subscription", args["subscription_id"],
            "--sku", args.get("sku") or "B1",
        ]
        if args.get("is_linux"):
            cmd += ["--is-linux"]
        text = await run_az(cmd, self._credentials, timeout_sec=120)
        return {"content": [{"type": "text", "text": text}]}


class CreateWebAppTool(BaseSkillTool):
    """Creates an Azure App Service web app."""

    name: ClassVar[str] = "create_web_app"
    description: ClassVar[str] = (
        "Create an Azure App Service web app bound to an existing App Service plan. "
        "Runtime examples: 'NODE:20-lts', 'PYTHON:3.11', 'DOTNETCORE:8.0', "
        "'JAVA:17:JAVASE', 'PHP:8.2'. Use 'az webapp list-runtimes' via "
        "`run_az_command` for the full list. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "plan": str,
        "subscription_id": str,
        "runtime": str | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = [
            "webapp", "create",
            "--name", args["name"],
            "--resource-group", args["resource_group"],
            "--plan", args["plan"],
            "--subscription", args["subscription_id"],
        ]
        if args.get("runtime"):
            cmd += ["--runtime", args["runtime"]]
        text = await run_az(cmd, self._credentials, timeout_sec=120)
        return {"content": [{"type": "text", "text": text}]}


class DeleteWebAppTool(BaseSkillTool):
    """Deletes an Azure App Service web app."""

    name: ClassVar[str] = "delete_web_app"
    description: ClassVar[str] = (
        "Delete an Azure App Service web app. "
        "Without confirmed=true this tool performs a dry run and returns a summary "
        "of what would be deleted — no changes are made. "
        "You MUST call `ask_user` first to get explicit user approval, "
        "then re-call this tool with confirmed=true to execute. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "subscription_id": str,
        "confirmed": bool | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("confirmed"):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "DRY RUN — no changes made.\n"
                        f"This would permanently delete web app '{args['name']}' "
                        f"in resource group '{args['resource_group']}' "
                        f"(subscription: {args['subscription_id']}).\n"
                        "To proceed: call `ask_user` to get explicit user approval, "
                        "then re-call this tool with confirmed=true."
                    ),
                }]
            }
        text = await run_az(
            [
                "webapp", "delete",
                "--name", args["name"],
                "--resource-group", args["resource_group"],
                "--subscription", args["subscription_id"],
            ],
            self._credentials,
        )
        return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------------------
# AKS (Azure Kubernetes Service) management
# ---------------------------------------------------------------------------


class ListAksClustersTool(BaseSkillTool):
    """Lists AKS clusters in a subscription or resource group."""

    name: ClassVar[str] = "list_aks_clusters"
    description: ClassVar[str] = (
        "List Azure Kubernetes Service (AKS) clusters in a subscription, "
        "optionally filtered by resource group. Returns name, location, "
        "Kubernetes version, and provisioning state. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "subscription_id": str,
        "resource_group": str | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = ["aks", "list", "--subscription", args["subscription_id"]]
        if args.get("resource_group"):
            cmd += ["--resource-group", args["resource_group"]]
        text = await run_az(cmd, self._credentials)
        return {"content": [{"type": "text", "text": text}]}


class CreateAksClusterTool(BaseSkillTool):
    """Creates a new AKS cluster."""

    name: ClassVar[str] = "create_aks_cluster"
    description: ClassVar[str] = (
        "Create a new Azure Kubernetes Service (AKS) cluster. "
        "node_count defaults to 3, node_vm_size defaults to 'Standard_DS2_v2'. "
        "kubernetes_version is optional; omit to use the default supported version. "
        "This operation can take 5-10 minutes. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "location": str,
        "subscription_id": str,
        "node_count": int | None,
        "node_vm_size": str | None,
        "kubernetes_version": str | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = [
            "aks", "create",
            "--name", args["name"],
            "--resource-group", args["resource_group"],
            "--location", args["location"],
            "--subscription", args["subscription_id"],
            "--node-count", str(args.get("node_count") or 3),
            "--node-vm-size", args.get("node_vm_size") or "Standard_DS2_v2",
            "--generate-ssh-keys",
        ]
        if args.get("kubernetes_version"):
            cmd += ["--kubernetes-version", args["kubernetes_version"]]
        text = await run_az(cmd, self._credentials, timeout_sec=600)
        return {"content": [{"type": "text", "text": text}]}


class GetAksCredentialsTool(BaseSkillTool):
    """Downloads kubeconfig credentials for an AKS cluster."""

    name: ClassVar[str] = "get_aks_credentials"
    description: ClassVar[str] = (
        "Download and merge kubeconfig credentials for an AKS cluster into "
        "~/.kube/config so that kubectl can connect. "
        "Returns confirmation or an error message. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "subscription_id": str,
        "overwrite_existing": bool | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = [
            "aks", "get-credentials",
            "--name", args["name"],
            "--resource-group", args["resource_group"],
            "--subscription", args["subscription_id"],
        ]
        if args.get("overwrite_existing"):
            cmd += ["--overwrite-existing"]
        text = await run_az(cmd, self._credentials)
        return {"content": [{"type": "text", "text": text}]}


class DeleteAksClusterTool(BaseSkillTool):
    """Deletes an AKS cluster."""

    name: ClassVar[str] = "delete_aks_cluster"
    description: ClassVar[str] = (
        "Delete an Azure Kubernetes Service (AKS) cluster and all its node pools. "
        "This is irreversible. Runs asynchronously with --no-wait. "
        "Without confirmed=true this tool performs a dry run and returns a summary "
        "of what would be deleted — no changes are made. "
        "You MUST call `ask_user` first to get explicit user approval, "
        "then re-call this tool with confirmed=true to execute. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "subscription_id": str,
        "confirmed": bool | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("confirmed"):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "DRY RUN — no changes made.\n"
                        f"This would permanently delete AKS cluster '{args['name']}' "
                        f"in resource group '{args['resource_group']}' "
                        f"(subscription: {args['subscription_id']}) and all its node pools.\n"
                        "To proceed: call `ask_user` to get explicit user approval, "
                        "then re-call this tool with confirmed=true."
                    ),
                }]
            }
        text = await run_az(
            [
                "aks", "delete",
                "--name", args["name"],
                "--resource-group", args["resource_group"],
                "--subscription", args["subscription_id"],
                "--yes", "--no-wait",
            ],
            self._credentials,
        )
        return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------------------
# Azure Container Registry management
# ---------------------------------------------------------------------------


class ListContainerRegistriesTool(BaseSkillTool):
    """Lists Azure Container Registries."""

    name: ClassVar[str] = "list_container_registries"
    description: ClassVar[str] = (
        "List Azure Container Registries (ACR) in a subscription, optionally "
        "filtered by resource group. Returns name, location, SKU, and login server. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "subscription_id": str,
        "resource_group": str | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = ["acr", "list", "--subscription", args["subscription_id"]]
        if args.get("resource_group"):
            cmd += ["--resource-group", args["resource_group"]]
        text = await run_az(cmd, self._credentials)
        return {"content": [{"type": "text", "text": text}]}


class CreateContainerRegistryTool(BaseSkillTool):
    """Creates a new Azure Container Registry."""

    name: ClassVar[str] = "create_container_registry"
    description: ClassVar[str] = (
        "Create a new Azure Container Registry (ACR). Name must be 5-50 alphanumeric "
        "characters and globally unique. SKU options: 'Basic' (default), "
        "'Standard', 'Premium'. Admin user is disabled by default; set "
        "admin_enabled=true to allow password-based docker login. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "location": str,
        "subscription_id": str,
        "sku": str | None,
        "admin_enabled": bool | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = [
            "acr", "create",
            "--name", args["name"],
            "--resource-group", args["resource_group"],
            "--location", args["location"],
            "--subscription", args["subscription_id"],
            "--sku", args.get("sku") or "Basic",
        ]
        if args.get("admin_enabled"):
            cmd += ["--admin-enabled", "true"]
        text = await run_az(cmd, self._credentials, timeout_sec=120)
        return {"content": [{"type": "text", "text": text}]}


class DeleteContainerRegistryTool(BaseSkillTool):
    """Deletes an Azure Container Registry."""

    name: ClassVar[str] = "delete_container_registry"
    description: ClassVar[str] = (
        "Delete an Azure Container Registry and all its images. This is irreversible. "
        "Without confirmed=true this tool performs a dry run and returns a summary "
        "of what would be deleted — no changes are made. "
        "You MUST call `ask_user` first to get explicit user approval, "
        "then re-call this tool with confirmed=true to execute. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "subscription_id": str,
        "confirmed": bool | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("confirmed"):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "DRY RUN — no changes made.\n"
                        f"This would permanently delete container registry '{args['name']}' "
                        f"in resource group '{args['resource_group']}' "
                        f"(subscription: {args['subscription_id']}) and ALL images inside it.\n"
                        "To proceed: call `ask_user` to get explicit user approval, "
                        "then re-call this tool with confirmed=true."
                    ),
                }]
            }
        text = await run_az(
            [
                "acr", "delete",
                "--name", args["name"],
                "--resource-group", args["resource_group"],
                "--subscription", args["subscription_id"],
                "--yes",
            ],
            self._credentials,
        )
        return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------------------
# Key Vault management
# ---------------------------------------------------------------------------


class ListKeyVaultsTool(BaseSkillTool):
    """Lists Azure Key Vaults."""

    name: ClassVar[str] = "list_key_vaults"
    description: ClassVar[str] = (
        "List Azure Key Vaults in a subscription, optionally filtered by "
        "resource group. Returns vault name, location, and URI. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "subscription_id": str,
        "resource_group": str | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = ["keyvault", "list", "--subscription", args["subscription_id"]]
        if args.get("resource_group"):
            cmd += ["--resource-group", args["resource_group"]]
        text = await run_az(cmd, self._credentials)
        return {"content": [{"type": "text", "text": text}]}


class CreateKeyVaultTool(BaseSkillTool):
    """Creates a new Azure Key Vault."""

    name: ClassVar[str] = "create_key_vault"
    description: ClassVar[str] = (
        "Create a new Azure Key Vault for storing secrets, keys, and certificates. "
        "Name must be 3-24 alphanumeric characters and globally unique. "
        "enable_rbac_authorization defaults to true (recommended over legacy access policies). "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "location": str,
        "subscription_id": str,
        "enable_rbac_authorization": bool | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        enable_rbac = args.get("enable_rbac_authorization")
        if enable_rbac is None:
            enable_rbac = True
        cmd = [
            "keyvault", "create",
            "--name", args["name"],
            "--resource-group", args["resource_group"],
            "--location", args["location"],
            "--subscription", args["subscription_id"],
            "--enable-rbac-authorization", "true" if enable_rbac else "false",
        ]
        text = await run_az(cmd, self._credentials, timeout_sec=120)
        return {"content": [{"type": "text", "text": text}]}


class DeleteKeyVaultTool(BaseSkillTool):
    """Deletes an Azure Key Vault."""

    name: ClassVar[str] = "delete_key_vault"
    description: ClassVar[str] = (
        "Delete an Azure Key Vault. By default, Key Vaults are soft-deleted "
        "and can be recovered within 90 days. Set purge=true to permanently delete "
        "(bypasses soft-delete — irreversible). "
        "Without confirmed=true this tool performs a dry run and returns a summary "
        "of what would be deleted — no changes are made. "
        "You MUST call `ask_user` first to get explicit user approval, "
        "then re-call this tool with confirmed=true to execute. "
        "Call `connect_azure` first if you have not done so this session."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "name": str,
        "resource_group": str,
        "subscription_id": str,
        "confirmed": bool | None,
        "purge": bool | None,
        "location": str | None,
    }

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("confirmed"):
            purge_note = (
                " The vault would also be PURGED (permanently, unrecoverable)."
                if args.get("purge")
                else " The vault would be soft-deleted and recoverable for 90 days."
            )
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "DRY RUN — no changes made.\n"
                        f"This would delete Key Vault '{args['name']}' "
                        f"in resource group '{args['resource_group']}' "
                        f"(subscription: {args['subscription_id']}).{purge_note}\n"
                        "To proceed: call `ask_user` to get explicit user approval, "
                        "then re-call this tool with confirmed=true."
                    ),
                }]
            }
        cmd = [
            "keyvault", "delete",
            "--name", args["name"],
            "--resource-group", args["resource_group"],
            "--subscription", args["subscription_id"],
        ]
        text = await run_az(cmd, self._credentials)
        if args.get("purge") and "az error" not in text:
            purge_cmd = [
                "keyvault", "purge",
                "--name", args["name"],
                "--subscription", args["subscription_id"],
            ]
            if args.get("location"):
                purge_cmd += ["--location", args["location"]]
            purge_text = await run_az(purge_cmd, self._credentials)
            text = f"{text}\nPurge result: {purge_text}"
        return {"content": [{"type": "text", "text": text}]}
